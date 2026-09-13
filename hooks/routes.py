"""HTTP boundary: size-limited uploads and opaque, authenticated file access."""
import hashlib
import math
import os
import re
import shutil
import time
from functools import wraps
from pathlib import Path
from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from .store import uid, valid_id
from .service import Service, seconds
from . import media, subtitles

CHUNK_SIZE = 8 * 1024 * 1024


def install(app, llm, settings):
    bp = Blueprint('hooks', __name__)
    services = {}

    def service():
        root = str(current_app.config.get('HOOKS_DATA_DIR') or os.environ.get('HOOKS_DATA_DIR') or Path.home() / '.youtube-downloader' / 'hooks')
        # Flask requests can initialize concurrently.
        with init_lock:
            if root not in services:
                services[root] = Service(root, llm, settings)
            s = services[root]
        if time.time() - s.last_cleanup > 3600:
            s.last_cleanup = time.time()
            s.store.cleanup()
        return s

    import threading
    init_lock = threading.Lock()

    def guarded(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except FileNotFoundError as e:
                return jsonify(error=str(e)), 404
            except (ValueError, KeyError, TypeError) as e:
                return jsonify(error=str(e)), 400
        return wrapper

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('올바른 요청 데이터가 필요합니다.')
        return data

    def idle(s, ident):
        state = s.store.read('jobs', ident)
        if state['busy']:
            raise ValueError('진행 중인 작업이 끝난 뒤 변경해주세요.')
        return state

    @bp.get('/hooks')
    def page():
        return render_template('hooks.html')

    @bp.get('/api/hooks/config')
    def config():
        return jsonify(chunk_size=CHUNK_SIZE, max_video_bytes=current_app.config.get('HOOKS_MAX_VIDEO_BYTES', 2 * 1024 ** 3),
                       max_srt_bytes=current_app.config.get('HOOKS_MAX_SRT_BYTES', 5 * 1024 ** 2), settings=settings())

    @bp.post('/api/hooks/uploads')
    @guarded
    def register_upload():
        s, data = service(), body()
        size = data.get('size')
        name = str(data.get('name', ''))
        if type(size) is not int or not 0 < size <= current_app.config.get('HOOKS_MAX_VIDEO_BYTES', 2 * 1024 ** 3):
            raise ValueError('영상 파일 용량이 허용 범위를 벗어납니다.')
        if Path(name).suffix.lower() not in ('.mp4', '.mov'):
            raise ValueError('MP4 또는 MOV 파일을 선택해주세요.')
        ident = uid()
        state = {'id': ident, 'name': Path(name).name[:200], 'size': size, 'chunks': {}, 'updated': time.time()}
        s.store.write('uploads', ident, state)
        return jsonify(id=ident, chunk_size=CHUNK_SIZE)

    @bp.put('/api/hooks/uploads/<ident>/chunks/<int:index>')
    @guarded
    def upload_chunk(ident, index):
        s = service()
        checksum = request.headers.get('X-Chunk-SHA256', '')
        if not re.fullmatch('[a-f0-9]{64}', checksum):
            raise ValueError('조각 체크섬이 필요합니다.')
        with s.store.lock:
            state = s.store.read('uploads', ident)
            if state.get('asset_id'):
                raise ValueError('이미 완료된 업로드입니다.')
            if index < 0 or index >= math.ceil(state['size'] / CHUNK_SIZE):
                raise ValueError('조각 번호가 올바르지 않습니다.')
            expected = min(CHUNK_SIZE, state['size'] - index * CHUNK_SIZE)
            if request.content_length is not None and request.content_length != expected:
                raise ValueError('조각 크기가 올바르지 않습니다.')
            directory = s.store.directory('uploads', ident)
            temp = directory / 'incoming.part'
            digest, received = hashlib.sha256(), 0
            try:
                with temp.open('wb') as out:
                    while True:
                        chunk = request.stream.read(min(1024 * 1024, expected + 1 - received))
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > expected:
                            raise ValueError('조각 크기가 너무 큽니다.')
                        digest.update(chunk)
                        out.write(chunk)
                if received != expected or digest.hexdigest() != checksum:
                    raise ValueError('조각 크기 또는 체크섬이 일치하지 않습니다. 다시 전송해주세요.')
                prior = state['chunks'].get(str(index))
                if prior and prior != checksum:
                    raise ValueError('이미 받은 조각과 내용이 다릅니다.')
                os.replace(temp, directory / f'{index}.chunk')
                state['chunks'][str(index)] = checksum
                state['updated'] = time.time()
                s.store.write('uploads', ident, state)
            finally:
                temp.unlink(missing_ok=True)
        return jsonify(ok=True, received=received)

    @bp.post('/api/hooks/uploads/<ident>/complete')
    @guarded
    def complete_upload(ident):
        s = service()
        with s.store.lock:
            state = s.store.read('uploads', ident)
            if state.get('asset_id'):
                return jsonify(asset_id=state['asset_id'])
            count = math.ceil(state['size'] / CHUNK_SIZE)
            if len(state['chunks']) != count:
                raise ValueError('아직 받지 못한 영상 조각이 있습니다.')
            asset_id = uid()
            directory = s.store.directory('assets', asset_id)
            directory.mkdir(parents=True)
            upload_dir = s.store.directory('uploads', ident)
            digest = hashlib.sha256()
            try:
                with (directory / 'video').open('wb') as out:
                    for i in range(count):
                        with (upload_dir / f'{i}.chunk').open('rb') as src:
                            while chunk := src.read(1024 * 1024):
                                digest.update(chunk)
                                out.write(chunk)
                if (directory / 'video').stat().st_size != state['size']:
                    raise ValueError('완성된 파일 크기가 일치하지 않습니다.')
                info = media.probe(directory / 'video')
                s.store.write('assets', asset_id, {'id': asset_id, 'name': state['name'], 'sha256': digest.hexdigest(),
                              'info': info, 'updated': time.time()})
                state.update(asset_id=asset_id, updated=time.time())
                s.store.write('uploads', ident, state)
                for part in upload_dir.glob('*.chunk'):
                    part.unlink()
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                raise
        return jsonify(asset_id=asset_id, info=info)

    @bp.delete('/api/hooks/uploads/<ident>')
    @guarded
    def cancel_upload(ident):
        s = service()
        with s.store.lock:
            s.store.read('uploads', ident)
            shutil.rmtree(s.store.directory('uploads', ident))
        return jsonify(ok=True)

    @bp.post('/api/hooks/jobs')
    @guarded
    def create():
        s, data = service(), body()
        state = s.create(data.get('url', ''), data.get('asset_id'))
        return jsonify(s.public(state)), 201

    @bp.get('/api/hooks/jobs')
    @guarded
    def history():
        s = service()
        states = []
        with s.store.lock:
            for p in (s.store.root / 'jobs').glob('*/state.json'):
                state = s.store.read('jobs', p.parent.name)
                states.append({k: state.get(k) for k in ('id', 'video_name', 'youtube_title', 'url', 'status', 'updated')})
        return jsonify(jobs=sorted(states, key=lambda x: x['updated'], reverse=True)[:100])

    @bp.get('/api/hooks/jobs/<ident>')
    @guarded
    def get(ident):
        return jsonify(service().public(service().store.read('jobs', ident)))

    @bp.post('/api/hooks/jobs/<ident>/subtitles')
    @guarded
    def upload_subtitles(ident):
        s = service()
        limit = current_app.config.get('HOOKS_MAX_SRT_BYTES', 5 * 1024 ** 2)
        raw = request.stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('SRT 파일이 너무 큽니다.')
        cues = subtitles.parse(raw)
        with s.store.lock:
            state = idle(s, ident)
            state.update(cues=cues, subtitle_source='uploaded', offset=0, alignment_confirmed=False,
                         candidates=[c for c in state['candidates'] if c['origin'] == 'manual'],
                         status='subtitles_ready', message='업로드한 자막을 적용했습니다.')
            s.store.write('jobs', ident, state)
        return jsonify(s.public(state))

    @bp.post('/api/hooks/jobs/<ident>/prepare')
    @guarded
    def prepare(ident):
        s = service()
        return jsonify(s.public(s.start(ident, 'preparing', lambda: s.prepare(ident))))

    @bp.post('/api/hooks/jobs/<ident>/analyze')
    @guarded
    def analyze(ident):
        s = service()
        state = s.store.read('jobs', ident)
        if not state['cues']:
            raise ValueError('먼저 자막을 확보해주세요.')
        return jsonify(s.public(s.start(ident, 'analyzing', lambda: s.analyze(ident))))

    @bp.patch('/api/hooks/jobs/<ident>')
    @guarded
    def update(ident):
        s, data = service(), body()
        with s.store.lock:
            state = idle(s, ident)
            if 'offset' in data:
                offset = seconds(data['offset'])
                if abs(offset) > 86400:
                    raise ValueError('보정값은 하루 이내로 입력해주세요.')
                if offset != state['offset']:
                    state.update(offset=offset, alignment_confirmed=False)
            if 'alignment_confirmed' in data:
                if data['alignment_confirmed'] and not state.get('preview_kind'):
                    raise ValueError('먼저 미리보기를 준비해주세요.')
                state['alignment_confirmed'] = data['alignment_confirmed'] is True
            s.store.write('jobs', ident, state)
        return jsonify(s.public(state))

    @bp.post('/api/hooks/jobs/<ident>/source')
    @guarded
    def replace_source(ident):
        s, data = service(), body()
        with s.store.lock:
            state = idle(s, ident)
            if 'url' in data:
                new_url = media.youtube_url(data['url'])
                if new_url != state['url']:
                    state.pop('youtube_duration', None)
                    state.pop('youtube_title', None)
                    state.pop('metadata_warning', None)
                if new_url != state['url'] and state['subtitle_source'] in ('youtube_auto', 'youtube_manual'):
                    state.update(cues=[], subtitle_source=None, candidates=[c for c in state['candidates'] if c['origin'] == 'manual'])
                state['url'] = new_url
            if 'asset_id' in data:
                asset_id = data['asset_id']
                asset = s.store.read('assets', asset_id) if asset_id else None
                state.update(asset_id=asset_id, info=asset['info'] if asset else None,
                             video_name=asset['name'] if asset else '')
            if not state['url'] and not state['asset_id']:
                raise ValueError('원본 영상이 필요합니다.')
            state.update(alignment_confirmed=False, last_media_use=time.time(), status='created', message='입력을 변경했습니다. 자막을 확인해주세요.')
            state.pop('preview_kind', None)
            for name in ('source', 'preview'):
                shutil.rmtree(s.store.directory('jobs', ident) / name, ignore_errors=True)
            s.store.write('jobs', ident, state)
        return jsonify(s.public(state))

    @bp.post('/api/hooks/jobs/<ident>/candidates')
    @guarded
    def manual(ident):
        s, data = service(), body()
        with s.store.lock:
            state = idle(s, ident)
            if not state['cues']:
                raise ValueError('자막을 확보한 작업에서 후보를 추가할 수 있습니다.')
            start, end = seconds(data.get('start')), seconds(data.get('end'))
            text = str(data.get('text', '')).strip()
            if not 0 <= start < end or not text or len(text) > 2000:
                raise ValueError('문구와 유효한 시작·종료 시간을 입력해주세요.')
            if state.get('info') and end > state['info']['duration']:
                raise ValueError('영상 범위를 벗어납니다.')
            state['candidates'].append({'id': uid(), 'origin': 'manual', 'kind': 'manual', 'text': text,
                                         'base_start': start, 'base_end': end, 'selected': True})
            s.store.write('jobs', ident, state)
        return jsonify(s.public(state))

    @bp.route('/api/hooks/jobs/<ident>/candidates/<cid>', methods=['PATCH', 'DELETE'])
    @guarded
    def edit_candidate(ident, cid):
        s = service()
        with s.store.lock:
            state = idle(s, ident)
            c = next((c for c in state['candidates'] if c['id'] == cid), None)
            if not c:
                raise FileNotFoundError('후보를 찾지 못했습니다.')
            if request.method == 'DELETE':
                state['candidates'].remove(c)
            else:
                data = body()
                if 'selected' in data:
                    c['selected'] = data['selected'] is True
                if 'start' in data or 'end' in data:
                    a, b = s.timing(state, c)
                    start, end = seconds(data.get('start', a)), seconds(data.get('end', b))
                    if not 0 <= start < end or (state.get('info') and end > state['info']['duration']):
                        raise ValueError('구간이 원본 영상 범위를 벗어납니다.')
                    offset = state['offset'] if c['origin'] == 'auto' else 0
                    c.update(base_start=start - offset, base_end=end - offset)
                if 'text' in data:
                    if c['origin'] != 'manual':
                        raise ValueError('자동 후보 원문은 수정하지 않습니다. 직접 후보를 추가해주세요.')
                    text = str(data['text']).strip()
                    if not text or len(text) > 2000:
                        raise ValueError('문구를 입력해주세요.')
                    c['text'] = text
            s.store.write('jobs', ident, state)
        return jsonify(s.public(state))

    @bp.post('/api/hooks/jobs/<ident>/preview')
    @guarded
    def preview(ident):
        s = service()
        data = request.get_json(silent=True) or {}
        return jsonify(s.public(s.start(ident, 'previewing', lambda: s.preview(ident, data.get('force') is True))))

    @bp.get('/api/hooks/jobs/<ident>/media')
    @guarded
    def stream(ident):
        return send_file(service().preview_path(ident), mimetype='video/mp4', conditional=True)

    @bp.post('/api/hooks/jobs/<ident>/export')
    @guarded
    def export(ident):
        s, data = service(), body()
        ids = data.get('candidate_ids')
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise ValueError('추출할 후보를 선택해주세요.')
        return jsonify(s.public(s.start(ident, 'exporting', lambda: s.export(ident, ids))))

    @bp.get('/api/hooks/jobs/<ident>/exports/<bid>/<name>')
    @guarded
    def result(ident, bid, name):
        s = service()
        state = s.store.read('jobs', ident)
        batch = next((b for b in state['exports'] if b['id'] == valid_id(bid)), None)
        if not batch:
            raise FileNotFoundError('결과를 찾지 못했습니다.')
        if name in ('clips.zip', 'clips.txt', 'clips.json') and not batch.get('complete'):
            return jsonify(error='모든 클립 처리가 끝난 뒤 다운로드할 수 있습니다.'), 409
        allowed = ['clips.json', 'clips.txt', 'clips.zip'] + [c['filename'] for c in batch['clips'] if c['status'] == 'finished']
        if name not in allowed:
            raise FileNotFoundError('파일을 찾지 못했습니다.')
        path = s.store.directory('jobs', ident) / 'exports' / bid / name
        return send_file(path, as_attachment=True, download_name=name)

    app.register_blueprint(bp)
    app.extensions['hooks_service'] = service
