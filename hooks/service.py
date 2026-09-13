"""Hook workflow; workers only persist completed phases and immutable exports."""
import hashlib
import json
import math
import os
import re
import shutil
import threading
import time
import unicodedata
import zipfile
from pathlib import Path
from .store import Store, uid
from . import media, subtitles, analysis


def seconds(value):
    try:
        value = float(value)
    except (TypeError, ValueError) as e:
        raise ValueError('시간을 초 단위 숫자로 입력해주세요.') from e
    if not math.isfinite(value):
        raise ValueError('유효한 시간을 입력해주세요.')
    return value


def stamp(value, filename=False):
    ms = round(value * 1000)
    h, rest = divmod(ms, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return f'{h:02d}-{m:02d}-{s:02d}.{ms:03d}' if filename else f'{h:02d}:{m:02d}:{s:02d}.{ms:03d}'


class Service:
    def __init__(self, root, llm, settings):
        self.store = Store(root)
        self.llm = llm
        self.settings = settings
        self.last_cleanup = time.time()

    def create(self, url='', asset_id=None, srt=None, existing=None):
        url = media.youtube_url(url)
        if not url and not asset_id:
            raise ValueError('원본 영상 또는 YouTube URL을 입력해주세요.')
        asset = self.store.read('assets', asset_id) if asset_id else None
        ident = existing or uid()
        if existing:
            old = self.store.read('jobs', ident)
            if old.get('busy'):
                raise ValueError('진행 중인 작업이 끝난 뒤 입력을 변경해주세요.')
        # Parse explicit subtitles before changing any state; never silently fall back.
        cues = subtitles.parse(srt) if srt is not None else None
        state = {'id': ident, 'url': url, 'asset_id': asset_id, 'video_name': asset['name'] if asset else '',
                 'info': asset['info'] if asset else None, 'status': 'created', 'busy': False,
                 'message': '자막 확인을 시작해주세요.', 'offset': 0, 'alignment_confirmed': False,
                 'cues': cues or [], 'subtitle_source': 'uploaded' if cues else None,
                 'candidates': [], 'exports': [], 'settings': self.settings(), 'usage': {},
                 'created': time.time(), 'updated': time.time(), 'last_media_use': time.time()}
        self.store.write('jobs', ident, state)
        return state

    def start(self, ident, operation, fn):
        with self.store.lock:
            state = self.store.read('jobs', ident)
            if state.get('busy'):
                return state
            self.store.update(ident, busy=True, status=operation, operation=operation, message='작업 대기 중')
        def worker():
            try:
                fn()
            except Exception as e:
                self.store.update(ident, status='error', message=str(e)[:1000], error_operation=operation)
            finally:
                self.store.update(ident, busy=False)
        threading.Thread(target=worker, daemon=True).start()
        return self.store.read('jobs', ident)

    def asset_path(self, state):
        path = self.store.directory('assets', state['asset_id']) / 'video'
        if not path.is_file():
            raise ValueError('임시 원본이 만료되었습니다. 원본 파일을 다시 연결해주세요. 분석 결과는 유지됩니다.')
        return path

    def prepare(self, ident):
        state = self.store.read('jobs', ident)
        if state['url'] and state['asset_id'] and not state.get('youtube_duration') and state['cues']:
            try:
                info = media.remote_info(state['url'])
                self.store.update(ident, youtube_duration=info.get('duration'), youtube_title=info.get('title'))
            except Exception:
                self.store.update(ident, metadata_warning='YouTube 영상 길이를 확인하지 못했습니다. 미리보기에서 시간을 확인해주세요.')
        if state['cues']:
            self.store.update(ident, status='subtitles_ready', message='자막 준비 완료. 후보 분석 또는 직접 추가가 가능합니다.')
            return
        if state['asset_id']:
            path = self.asset_path(state)
            tracks = state['info']['subtitles']
            if tracks:
                cues = subtitles.parse(media.embedded(path, tracks[0]))
                self.store.update(ident, cues=cues, subtitle_source='embedded', status='subtitles_ready', message='영상 내 자막을 가져왔습니다.')
                return
        if not state['url']:
            self.store.update(ident, status='needs_input', message='원본 YouTube URL 또는 SRT를 추가해주세요.')
            return
        self.store.update(ident, message='YouTube 한국어 자막 확인 중')
        info = media.remote_info(state['url'])
        self.store.update(ident, youtube_duration=info.get('duration'), youtube_title=info.get('title'))
        result = media.remote_subtitle(info)
        if result is None:
            self.store.update(ident, status='no_subtitles', message='사용 가능한 한국어 자막이 없습니다. AI를 호출하지 않았습니다.')
            return
        raw, fmt, source = result
        cues = subtitles.parse(raw, fmt)
        self.store.update(ident, cues=cues, subtitle_source=source, status='subtitles_ready', message='자막 준비 완료. 후보를 분석하거나 직접 추가해주세요.')

    def analyze(self, ident):
        state = self.store.read('jobs', ident)
        if not state['cues']:
            raise ValueError('먼저 자막을 확보해주세요.')
        matches, usage = analysis.analyze(state['cues'], state['settings'], self.store.root / 'cache', self.llm,
            lambda message, usage: self.store.update(ident, message=message, usage=dict(usage)))
        ids = {c['id']: i for i, c in enumerate(state['cues'])}
        current = {c['id']: c for c in state['candidates']}
        candidates = [c for c in state['candidates'] if c['origin'] == 'manual']
        for match in matches:
            part = state['cues'][ids[match['start_id']]:ids[match['end_id']] + 1]
            cid = hashlib.sha256(f"{match['start_id']}:{match['end_id']}".encode()).hexdigest()[:32]
            if cid in current:
                candidates.append(current[cid])
                continue
            start = min(c['start_ms'] for c in part) / 1000
            end = max(c['end_ms'] for c in part) / 1000
            base_start = max(0, start - .2)
            base_end = min(state['info']['duration'], end + .2) if state['info'] else end + .2
            candidates.append({'id': cid, 'origin': 'auto', 'kind': match['kind'],
                'start_id': match['start_id'], 'end_id': match['end_id'],
                'text': ' '.join(c['text'] for c in part), 'subtitle_start': start, 'subtitle_end': end,
                'base_start': base_start, 'base_end': base_end, 'selected': False})
        self.store.update(ident, candidates=candidates, usage=usage, status='ready',
                          message=f'자동 후보 {len(matches)}개를 찾았습니다.' if matches else '자동 후보가 없습니다. 필요한 발언을 직접 추가할 수 있습니다.')

    def timing(self, state, candidate):
        offset = state['offset'] if candidate['origin'] == 'auto' else 0
        return candidate['base_start'] + offset, candidate['base_end'] + offset

    def public(self, state):
        state = json.loads(json.dumps(state))
        state['cue_count'] = len(state.pop('cues'))
        for c in state['candidates']:
            c['start'], c['end'] = self.timing(state, c)
        state['candidates'].sort(key=lambda c: c['start'])
        state['needs_alignment'] = bool(state['asset_id'] and state['url'])
        state['duration_warning'] = bool(state.get('info') and state.get('youtube_duration') and
            abs(state['info']['duration'] - state['youtube_duration']) > 1)
        state['source_missing'] = bool(state['asset_id'] and not (self.store.directory('assets', state['asset_id']) / 'video').exists())
        return state

    def source(self, ident):
        state = self.store.read('jobs', ident)
        self.store.update(ident, last_media_use=time.time())
        if state['asset_id']:
            return self.asset_path(state)
        path = media.download(state['url'], self.store.directory('jobs', ident) / 'source',
                              lambda p: self.store.update(ident, message='영상 다운로드 ' + p))
        self.store.update(ident, info=media.probe(path))
        return path

    def preview(self, ident, force=False):
        path = self.source(ident)
        info = media.probe(path)
        state = self.store.read('jobs', ident)
        self.store.update(ident, info=info)
        if not force and info['video_codec'] == 'h264' and info['audio_codec'] in ('aac', None):
            self.store.update(ident, preview_kind='source', status='ready', message='원본 미리보기 준비 완료')
            return
        directory = self.store.directory('jobs', ident) / 'preview'
        directory.mkdir(exist_ok=True)
        output = directory / 'preview.mp4'
        if not output.exists():
            self.store.update(ident, message='호환 미리보기 생성 중 · 원본으로 최종 추출합니다.')
            media.encode(path, output, preview=True)
        self.store.update(ident, preview_kind='proxy', status='ready', message='호환 미리보기 준비 완료')

    def preview_path(self, ident):
        state = self.store.read('jobs', ident)
        self.store.update(ident, last_media_use=time.time())
        if state.get('preview_kind') == 'proxy':
            path = self.store.directory('jobs', ident) / 'preview' / 'preview.mp4'
        elif state['asset_id']:
            path = self.asset_path(state)
        else:
            paths = list((self.store.directory('jobs', ident) / 'source').glob('source.*'))
            path = next((p for p in paths if p.suffix in ('.mp4', '.mkv', '.webm', '.mov')), None)
        if path is None or not path.is_file():
            raise FileNotFoundError('미리보기를 다시 준비해주세요.')
        return path

    def export(self, ident, requested):
        source = self.source(ident)
        state = self.store.read('jobs', ident)
        info = media.probe(source)
        if not state['cues']:
            raise ValueError('먼저 자막을 확보해주세요.')
        asset = self.store.read('assets', state['asset_id']) if state['asset_id'] else {}
        if state['asset_id'] and state['url'] and not state['alignment_confirmed']:
            raise ValueError('미리보기에서 영상과 자막 시간의 일치를 확인해주세요.')
        candidates = [c for c in state['candidates'] if c['id'] in requested]
        if len(candidates) != len(set(requested)) or not candidates:
            raise ValueError('추출할 후보를 선택해주세요.')
        bid = uid()
        directory = self.store.directory('jobs', ident) / 'exports' / bid
        directory.mkdir(parents=True)
        records = []
        batch = {'id': bid, 'created': time.time(), 'clips': records, 'total': len(candidates), 'complete': False}
        self.store.update(ident, exports=state['exports'] + [batch])
        for i, c in enumerate(candidates):
            start, end = self.timing(state, c)
            record = {'id': c['id'], 'origin': c['origin'], 'kind': c['kind'], 'text': c['text'],
                      'video_source': state['video_name'] if state['asset_id'] else state['url'],
                      'video_asset_id': state['asset_id'], 'video_sha256': asset.get('sha256'),
                      'subtitle_source': state['subtitle_source'], 'subtitle_url': state['url'] if state['subtitle_source'].startswith('youtube') else None,
                      'subtitle_start': c.get('subtitle_start'), 'subtitle_end': c.get('subtitle_end'),
                      'offset': state['offset'] if c['origin'] == 'auto' else 0, 'start': start, 'end': end}
            label = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '', unicodedata.normalize('NFC', c['text']))[:40].strip(' .') or '후킹'
            name = f'{label}_{stamp(start, True)}-{stamp(end, True)}.mp4'
            if (directory / name).exists():
                name = f'{Path(name).stem}_{i + 1}.mp4'
            self.store.update(ident, message=f'클립 추출 {i + 1}/{len(candidates)} · {label}')
            try:
                if not 0 <= start < end <= info['duration']:
                    raise ValueError('구간이 원본 영상 범위를 벗어납니다. 시간을 조정해주세요.')
                media.encode(source, directory / name, start, end)
                record.update(filename=name, status='finished')
            except Exception as e:
                record.update(status='error', error=str(e))
            records.append(record)
            # Write after every clip; a restart preserves successful work.
            self._manifest(directory, records)
            latest = self.store.read('jobs', ident)
            latest['exports'][-1] = batch
            self.store.update(ident, exports=latest['exports'])
        batch['complete'] = True
        latest = self.store.read('jobs', ident)
        latest['exports'][-1] = batch
        self.store.update(ident, exports=latest['exports'], status='ready', message='추출 완료 · 실패한 항목은 시간을 확인한 뒤 다시 시도해주세요.' if any(r['status'] == 'error' for r in records) else '선택한 클립을 모두 저장했습니다.')

    def _manifest(self, directory, records):
        contents = {
            'clips.json': json.dumps(records, ensure_ascii=False, indent=2),
            'clips.txt': '\n\n'.join(f"{r['text']}\n{stamp(r['start'])} – {stamp(r['end'])}\n{r.get('filename', r.get('error'))}" for r in records),
        }
        for name, text in contents.items():
            temp = directory / (name + '.tmp')
            temp.write_text(text, encoding='utf-8')
            os.replace(temp, directory / name)
        temp = directory / 'clips.zip.tmp'
        with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in ['clips.json', 'clips.txt'] + [r['filename'] for r in records if r['status'] == 'finished']:
                archive.write(directory / name, name)
        os.replace(temp, directory / 'clips.zip')
