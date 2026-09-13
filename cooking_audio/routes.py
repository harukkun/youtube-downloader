"""Authenticated cooking-audio API. Shared upload transport stays under /api/hooks."""
import time
from functools import wraps
from flask import Blueprint, current_app, jsonify, request, send_file
import yt_dlp
from hooks import media, subtitles
from hooks.service import seconds
from hooks.store import valid_id
from .service import Service
from . import asr


def install(app):
    bp = Blueprint('cooking_audio', __name__, url_prefix='/api/cooking-audio')

    def service():
        return Service(current_app.extensions['hooks_service']())

    def guarded(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except FileNotFoundError as e:
                return jsonify(error=str(e)), 404
            except (ValueError, KeyError, TypeError) as e:
                return jsonify(error=str(e)), 400
        return wrapped

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('JSON 객체가 필요합니다.')
        return data

    def idle(s, ident):
        state = s.get(ident)
        if state['busy']:
            raise ValueError('진행 중인 작업이 끝난 뒤 변경해주세요.')
        return state

    @bp.get('/config')
    def config():
        cfg = asr.config()
        return jsonify(asr_available=cfg['available'], asr_model=cfg['model'])

    @bp.route('/jobs', methods=['GET', 'POST'])
    @guarded
    def jobs():
        s = service()
        if request.method == 'POST':
            return jsonify(s.public(s.create_cooking(body()))), 201
        states = []
        for path in (s.store.root/'jobs').glob('*/state.json'):
            state = s.store.read('jobs', path.parent.name)
            if state.get('feature') == 'cooking-audio':
                states.append({k: state.get(k) for k in ('id', 'video_name', 'youtube_title', 'url', 'updated', 'status')})
        return jsonify(jobs=sorted(states, key=lambda x: x['updated'], reverse=True)[:100])

    @bp.route('/jobs/<ident>', methods=['GET', 'PATCH'])
    @guarded
    def job(ident):
        s = service()
        with s.store.lock:
            state = s.get(ident)
            if request.method == 'PATCH':
                state = idle(s, ident)
                data = body()
                if 'offset' in data:
                    offset = seconds(data['offset'])
                    if abs(offset) > 86400:
                        raise ValueError('시간 보정은 하루 이내로 입력해주세요.')
                    state.update(offset=offset, alignment_confirmed=False)
                if 'alignment_confirmed' in data:
                    if not state.get('preview_kind'):
                        raise ValueError('먼저 미리보기를 준비해주세요.')
                    state['alignment_confirmed'] = data['alignment_confirmed'] is True
                if 'reference' in data:
                    reference = str(data['reference'])
                    if len(reference) > 20000:
                        raise ValueError('참고 레시피는 20,000자 이내로 입력해주세요.')
                    state['reference'] = reference
                if 'reference_enabled' in data:
                    state['reference_enabled'] = data['reference_enabled'] is True
                if 'order' in data:
                    order = data['order']
                    ids = [c['id'] for c in state['candidates']]
                    if not isinstance(order, list) or len(order) != len(ids) or set(order) != set(ids):
                        raise ValueError('모든 후보를 중복 없이 지정해주세요.')
                    lookup = {c['id']: c for c in state['candidates']}
                    state['candidates'] = [lookup[i] for i in order]
                s.store.write('jobs', ident, state)
        return jsonify(s.public(state))

    @bp.post('/jobs/<ident>/source')
    @guarded
    def source(ident):
        s, data = service(), body()
        with s.store.lock:
            state = idle(s, ident)
            asset = s.store.read('assets', data.get('asset_id'))
            if asset['sha256'] != state['source_fingerprint']:
                return jsonify(error='다른 원본입니다. 새 작업을 만들어주세요.'), 409
            state.update(asset_id=asset['id'], info=asset['info'], last_media_use=time.time())
            s.store.write('jobs', ident, state)
        return jsonify(s.public(state))

    @bp.post('/jobs/<ident>/subtitles')
    @guarded
    def upload_subtitles(ident):
        s = service()
        limit = current_app.config.get('HOOKS_MAX_SRT_BYTES', 5*1024**2)
        raw = request.stream.read(limit+1)
        if len(raw) > limit:
            raise ValueError('SRT 파일 용량이 제한을 초과합니다.')
        cues = subtitles.parse(raw, preserve_context=True)
        with s.store.lock:
            state = idle(s, ident)
            s.add_transcript(state, cues, 'uploaded')
            state.update(status='subtitles_ready', alignment_confirmed=False,
                         message='새 자막을 연결했습니다. 기존 후보는 보존하며 새 분석 결과를 추가합니다.')
            s.store.write('jobs', ident, state)
        return jsonify(s.public(state))

    @bp.post('/jobs/<ident>/<operation>')
    @guarded
    def operation(ident, operation):
        s = service()
        s.get(ident)
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            raise ValueError('JSON 객체가 필요합니다.')
        if operation == 'cancel':
            return jsonify(s.public(s.store.update(ident, cancel_requested=True, message='취소 요청 중 · 진행 중인 단계 종료 후 멈춥니다.')))
        if operation == 'prepare':
            fn, status = lambda: s.prepare(ident), 'preparing'
        elif operation == 'analyze':
            fn, status = lambda: s.analyze(ident), 'analyzing'
        elif operation == 'asr':
            fn, status = lambda: s.recognize(ident, data.get('start'), data.get('end')), 'transcribing'
        elif operation == 'preview':
            fn, status = lambda: s.preview(ident, data.get('force') is True), 'previewing'
        elif operation == 'references':
            def fn():
                state = s.get(ident)
                if not state['url']:
                    raise ValueError('YouTube URL이 필요합니다. 참고 레시피를 직접 붙여넣어도 됩니다.')
                sources = {}
                warning = ''
                try:
                    info = media.remote_info(state['url'])
                    sources['description'] = (info.get('description') or '')[:12000]
                    # Only an explicitly pinned comment qualifies; never substitute the first result.
                    with yt_dlp.YoutubeDL({'quiet': True, 'skip_download': True, 'getcomments': True,
                        'socket_timeout': 20, 'retries': 1, 'extractor_retries': 1,
                        'extractor_args': {'youtube': {'max_comments': ['20'], 'comment_sort': ['top']}}}) as y:
                        details = y.extract_info(state['url'], download=False)
                    pinned = next((c for c in details.get('comments', []) if c.get('is_pinned')), None)
                    sources['pinned_comment'] = pinned.get('text', '')[:8000] if pinned else ''
                    if not pinned:
                        warning = '고정 댓글을 확인하지 못했습니다. 필요한 레시피는 직접 붙여넣어주세요.'
                except Exception:
                    warning = '참고 자료를 일부 가져오지 못했습니다. 직접 붙여넣거나 참고 없이 분석할 수 있습니다.'
                state = s.get(ident)
                s.store.update(ident, reference_sources=sources,
                               reference=state['reference'] or '\n\n'.join(v for v in sources.values() if v),
                               status='ready', message=warning or '설명과 고정 댓글을 가져왔습니다. 내용을 확인한 뒤 참고 사용을 선택하세요.')
            status = 'references'
        elif operation == 'export':
            ids = data.get('candidate_ids')
            if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
                raise ValueError('추출할 후보를 선택해주세요.')
            fn, status = lambda: s.export(ident, ids), 'exporting'
        else:
            raise FileNotFoundError('지원하지 않는 작업입니다.')
        return jsonify(s.public(s.start(ident, status, fn)))

    @bp.post('/jobs/<ident>/candidates')
    @guarded
    def manual(ident):
        s, data = service(), body()
        with s.store.lock:
            state = idle(s, ident)
            state['candidates'].append(s.manual(state, data))
            s.store.write('jobs', ident, state)
        return jsonify(s.public(state))

    @bp.route('/jobs/<ident>/candidates/<cid>', methods=['PATCH', 'DELETE'])
    @guarded
    def edit(ident, cid):
        s = service()
        with s.store.lock:
            state = idle(s, ident)
            s.edit(state, cid, body() if request.method == 'PATCH' else None)
            s.store.write('jobs', ident, state)
        return jsonify(s.public(state))

    @bp.post('/jobs/<ident>/candidates/<cid>/split')
    @guarded
    def split(ident, cid):
        s, data = service(), body()
        with s.store.lock:
            state = idle(s, ident)
            s.split(state, cid, data.get('time'))
            s.store.write('jobs', ident, state)
        return jsonify(s.public(state))

    @bp.post('/jobs/<ident>/merge')
    @guarded
    def merge(ident):
        s, data = service(), body()
        ids = data.get('candidate_ids')
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise ValueError('병합할 후보 목록이 필요합니다.')
        with s.store.lock:
            state = idle(s, ident)
            s.merge(state, ids)
            s.store.write('jobs', ident, state)
        return jsonify(s.public(state))

    @bp.get('/jobs/<ident>/media')
    @guarded
    def stream(ident):
        s = service()
        s.get(ident)
        return send_file(s.preview_path(ident), mimetype='video/mp4', conditional=True)

    @bp.get('/jobs/<ident>/exports/<bid>/<name>')
    @guarded
    def download(ident, bid, name):
        s = service()
        state = s.get(ident)
        batch = next((b for b in state['exports'] if b['id'] == valid_id(bid)), None)
        if not batch:
            raise FileNotFoundError('결과를 찾지 못했습니다.')
        manifests = ['dialogues.zip', 'dialogues.txt', 'dialogues.json']
        if name in manifests and not batch['complete']:
            return jsonify(error='모든 음원 처리가 완료된 뒤 다운로드할 수 있습니다.'), 409
        if name not in manifests + [c['filename'] for c in batch['clips'] if c['status'] == 'finished']:
            raise FileNotFoundError('파일을 찾지 못했습니다.')
        return send_file(s.store.directory('jobs', ident)/'exports'/bid/name, as_attachment=True, download_name=name)

    app.register_blueprint(bp)
