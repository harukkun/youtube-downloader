"""Cooking jobs use shared assets but independent transcripts, edits and exports."""
import copy
import hashlib
import json
import os
import re
import threading
import time
import unicodedata
import zipfile
from pathlib import Path
from hooks.service import Service as MediaService, seconds, stamp
from hooks.store import uid
from hooks import media, subtitles
from . import analysis, asr


def bounded(start, end, duration):
    a, b = seconds(start), seconds(end)
    if not 0 <= a < b <= duration:
        raise ValueError('구간이 원본 영상 범위를 벗어납니다.')
    return a, b


class Service(MediaService):
    def __init__(self, shared):
        self.store, self.llm, self.settings = shared.store, shared.llm, shared.settings
        self.last_cleanup = shared.last_cleanup

    def get(self, ident):
        state = self.store.read('jobs', ident)
        if state.get('feature') != 'cooking-audio':
            raise FileNotFoundError('조리 대사 작업을 찾지 못했습니다.')
        return state

    def create_cooking(self, data):
        source = None
        if data.get('hook_job'):
            source = self.store.read('jobs', data['hook_job'])
            if source.get('feature'):
                raise ValueError('후킹 클립 작업을 선택해주세요.')
            if source['busy']:
                raise ValueError('후킹 작업이 완료된 뒤 가져와주세요.')
        state = super().create(source['url'] if source else data.get('url', ''),
                               source['asset_id'] if source else data.get('asset_id'))
        state.update(feature='cooking-audio', transcripts=[], deleted=[], reference='', reference_enabled=False,
                     reference_sources={}, cancel_requested=False)
        if source:
            state.update(offset=source['offset'], info=source.get('info'), youtube_duration=source.get('youtube_duration'),
                         youtube_title=source.get('youtube_title'), source_hook_job=source['id'])
            if source['cues']:
                self.add_transcript(state, copy.deepcopy(source['cues']), source['subtitle_source'])
            # Hard links allow each job's temporary retention to be independent without copying video bytes.
            origin = self.store.directory('jobs', source['id']) / 'source'
            target = self.store.directory('jobs', state['id']) / 'source'
            if origin.exists():
                target.mkdir(exist_ok=True)
                for path in origin.glob('source.*'):
                    if path.suffix in ('.mp4', '.mov', '.mkv', '.webm'):
                        os.link(path, target / path.name)
        if state.get('asset_id'):
            state['source_fingerprint'] = self.store.read('assets', state['asset_id'])['sha256']
        else:
            state['source_fingerprint'] = state['url']
        self.store.write('jobs', state['id'], state)
        return state

    def add_transcript(self, state, cues, source, partial=False):
        version = subtitles.fingerprint(cues, {'source': source, 'feature': 'cooking-audio'})
        if not any(t['id'] == version for t in state['transcripts']):
            state['transcripts'].append({'id': version, 'cues': cues, 'source': source, 'partial': partial})
        if not partial:
            state.update(cues=cues, subtitle_source=source, active_transcript=version)
        state['latest_transcript'] = version

    def cancelled(self, ident):
        return self.get(ident).get('cancel_requested', False)

    def start(self, ident, operation, fn):
        with self.store.lock:
            state = self.get(ident)
            if state['busy']:
                return state
            self.store.update(ident, cancel_requested=False)
            return super().start(ident, operation, fn)

    def prepare(self, ident):
        state = self.get(ident)
        if state['cues']:
            self.store.update(ident, status='subtitles_ready', message='대사 자료 준비 완료. 조리 대사를 분석해주세요.')
            return
        cues, source = None, None
        if state['asset_id'] and state['info']['subtitles']:
            cues = subtitles.parse(media.embedded(self.asset_path(state), state['info']['subtitles'][0]), preserve_context=True)
            source = 'embedded'
        if not cues and state['url']:
            # Retrieval failure is an error, never treated as proof of absent subtitles.
            info = media.remote_info(state['url'])
            self.store.update(ident, youtube_duration=info.get('duration'), youtube_title=info.get('title'))
            result = media.remote_subtitle(info)
            if result:
                raw, fmt, source = result
                cues = subtitles.parse(raw, fmt, preserve_context=True)
        if cues:
            state = self.get(ident)
            self.add_transcript(state, cues, source)
            state.update(status='subtitles_ready', message='자막 준비 완료. 조리 대사를 분석해주세요.')
            self.store.write('jobs', ident, state)
        else:
            self.recognize(ident)

    def recognize(self, ident, start=None, end=None):
        path = self.source(ident)
        info = media.probe(path)
        if not info['audio_codec']:
            raise ValueError('원본에 음성 트랙이 없습니다.')
        partial = start is not None or end is not None
        a, b = bounded(start if partial else 0, end if partial else info['duration'], info['duration'])
        self.store.update(ident, info=info, status='transcribing')
        state = self.get(ident)
        digest = self.store.read('assets', state['asset_id'])['sha256'] if state['asset_id'] else None
        cues, cached = asr.transcribe(path, a, b, self.store.root / 'cache',
            lambda m: self.store.update(ident, message=m), lambda: self.cancelled(ident), digest)
        if self.cancelled(ident):
            raise InterruptedError('음성 인식을 취소했습니다.')
        state = self.get(ident)
        self.add_transcript(state, cues, 'asr', partial)
        if partial:
            next(t for t in state['transcripts'] if t['id'] == state['latest_transcript'])['range'] = [a, b]
        state.update(status='subtitles_ready', asr_cache_hit=cached,
                     message='음성 인식 완료 · 최신 자료 분석으로 대안 후보를 추가하세요.' if partial else '전체 음성 인식 완료. 조리 대사를 분석해주세요.')
        self.store.write('jobs', ident, state)

    def analyze(self, ident):
        state = self.get(ident)
        tid = state.get('latest_transcript')
        transcript = next((t for t in state['transcripts'] if t['id'] == tid), None)
        if not transcript:
            raise ValueError('먼저 자막 또는 음성 인식 자료를 확보해주세요.')
        reference = state['reference'] if state['reference_enabled'] else ''
        matches, usage = analysis.analyze(transcript['cues'], state['settings'], reference,
            self.store.root / 'cache', self.llm, lambda m, u: self.store.update(ident, message=m, usage=u),
            lambda: self.cancelled(ident))
        if self.cancelled(ident):
            raise InterruptedError('분석을 취소했습니다.')
        cues = transcript['cues']
        ids = {c['id']: i for i, c in enumerate(cues)}
        state = self.get(ident)
        known = {c['id'] for c in state['candidates']} | set(state['deleted'])
        count = 0
        for m in matches:
            cid = hashlib.sha256(f"{tid}:{m['start_id']}:{m['end_id']}".encode()).hexdigest()[:32]
            if cid in known:
                continue
            part = cues[ids[m['start_id']]:ids[m['end_id']]+1]
            text = ' '.join(re.sub(r'\[(?:음악|박수|웃음|Music|Applause)\]', '', c['text'], flags=re.I).strip() for c in part if not c.get('context_only'))
            if not text.strip():
                continue
            start, end = min(c['start_ms'] for c in part)/1000, max(c['end_ms'] for c in part)/1000
            candidate = dict(m, id=cid, origin=transcript['source'], transcript_id=tid, text=text, original_text=text,
                subtitle_start=start, subtitle_end=end, base_start=max(0, start-.1), base_end=end+.1,
                coordinate='source' if transcript['source'] == 'asr' else 'subtitle', selected=not transcript['partial'],
                alternative=transcript['partial'])
            if transcript['source'] == 'asr':
                candidate['check'] = True
            if transcript.get('range'):
                candidate['base_start'] = max(candidate['base_start'], transcript['range'][0])
                candidate['base_end'] = min(candidate['base_end'], transcript['range'][1])
            if state.get('info'):
                candidate['base_end'] = min(candidate['base_end'], state['info']['duration'])
            state['candidates'].append(candidate)
            count += 1
        state.update(usage=usage, status='ready', message=f'새 조리 대사 {count}개 추가 · 기존 편집 내용은 유지했습니다.')
        self.store.write('jobs', ident, state)

    def timing(self, state, candidate):
        shift = state['offset'] if candidate.get('coordinate') == 'subtitle' else 0
        return candidate['base_start'] + shift, candidate['base_end'] + shift

    def public(self, state):
        public = super().public(state)
        # Preserve the user's intended use order rather than re-sorting by source time.
        lookup = {c['id']: c for c in public['candidates']}
        public['candidates'] = [lookup[c['id']] for c in state['candidates']]
        public['transcript_count'] = len(public.pop('transcripts'))
        public['needs_alignment'] = bool(state['asset_id'] and state['url'] and any(c.get('coordinate') == 'subtitle' for c in state['candidates']))
        selected = [c for c in public['candidates'] if c['selected']]
        public['overlapping'] = [c['id'] for c in selected if any(d['id'] != c['id'] and c['start'] < d['end'] and d['start'] < c['end'] for d in selected)]
        seen = {}
        for c in public['candidates']:
            key = re.sub(r'\W', '', c['original_text'])
            c['duplicate'] = key in seen
            seen[key] = c['id']
        public['selected_duration'] = sum(c['end']-c['start'] for c in selected)
        return public

    def manual(self, state, data):
        duration = state['info']['duration'] if state.get('info') else float('inf')
        a, b = bounded(data.get('start'), data.get('end'), duration)
        text = str(data.get('text', '')).strip()
        if not text or len(text) > 2000:
            raise ValueError('문구는 1~2000자로 입력해주세요.')
        return dict(id=uid(), origin='manual', coordinate='source', kind='sequence', text=text,
                    original_text=text, base_start=a, base_end=b, selected=True, reaction=False, check=False)

    def edit(self, state, cid, data=None):
        c = next((c for c in state['candidates'] if c['id'] == cid), None)
        if not c:
            raise FileNotFoundError('후보를 찾지 못했습니다.')
        if data is None:
            state['deleted'].append(cid)
            state['candidates'].remove(c)
            return
        if 'text' in data:
            text = str(data['text']).strip()
            if not text or len(text) > 2000:
                raise ValueError('문구는 1~2000자로 입력해주세요.')
            c['text'] = text
        if 'selected' in data:
            c['selected'] = data['selected'] is True
        if 'start' in data or 'end' in data:
            a, b = self.timing(state, c)
            a, b = bounded(data.get('start', a), data.get('end', b), state['info']['duration'] if state.get('info') else float('inf'))
            shift = state['offset'] if c['coordinate'] == 'subtitle' else 0
            c.update(base_start=a-shift, base_end=b-shift)
        c['edited'] = True

    def split(self, state, cid, point):
        c = next((c for c in state['candidates'] if c['id'] == cid), None)
        if not c:
            raise FileNotFoundError('후보를 찾지 못했습니다.')
        a, b = self.timing(state, c)
        point = seconds(point)
        if not a < point < b:
            raise ValueError('후보 구간 내부의 분할 시간을 지정해주세요.')
        index = state['candidates'].index(c)
        children = []
        for start, end in ((a, point), (point, b)):
            child = copy.deepcopy(c)
            child.update(id=uid(), coordinate='source', base_start=start, base_end=end, edited=True,
                         parent_ids=[cid], text_review=True)
            children.append(child)
        self.edit(state, cid)
        state['candidates'][index:index] = children

    def merge(self, state, ids):
        chosen = [c for c in state['candidates'] if c['id'] in ids]
        positions = [state['candidates'].index(c) for c in chosen]
        if len(chosen) < 2 or len(chosen) != len(set(ids)) or positions != list(range(min(positions), max(positions)+1)):
            raise ValueError('목록에서 인접한 후보 두 개 이상을 선택해주세요.')
        times = [self.timing(state, c) for c in chosen]
        a, b = min(t[0] for t in times), max(t[1] for t in times)
        merged = self.manual(state, {'start': a, 'end': b, 'text': ' '.join(c['text'] for c in chosen)[:2000]})
        merged.update(origin='edited', original_text=' '.join(c['original_text'] for c in chosen), parent_ids=ids,
                      reaction=any(c.get('reaction') for c in chosen), text_review=True)
        index = positions[0]
        for c in chosen:
            self.edit(state, c['id'])
        state['candidates'].insert(index, merged)

    def export(self, ident, requested):
        source = self.source(ident)
        state = self.get(ident)
        info = media.probe(source)
        if not info['audio_codec']:
            raise ValueError('원본에 음성 트랙이 없습니다.')
        chosen = [c for c in state['candidates'] if c['id'] in requested]
        if not chosen or len(chosen) != len(set(requested)):
            raise ValueError('유효한 후보를 선택해주세요.')
        if state['asset_id'] and state['url'] and any(c['coordinate'] == 'subtitle' for c in chosen) and not state['alignment_confirmed']:
            raise ValueError('미리보기에서 자막 시간 일치를 확인해주세요.')
        bid = uid()
        directory = self.store.directory('jobs', ident) / 'exports' / bid
        directory.mkdir(parents=True)
        batch = dict(id=bid, created=time.time(), complete=False, total=len(chosen), clips=[], requested=requested)
        state['exports'].append(batch)
        self.store.update(ident, exports=state['exports'], info=info)
        names = set()
        for order, c in enumerate(chosen, 1):
            if self.cancelled(ident):
                self.store.update(ident, status='interrupted', message='추출 취소 · 완료된 개별 음원은 보존했습니다.')
                return
            a, b = self.timing(state, c)
            record = dict(c, order=order, start=a, end=b, offset=state['offset'] if c['coordinate'] == 'subtitle' else 0,
                          video_source=state['video_name'] or state['url'], source_fingerprint=state['source_fingerprint'])
            label = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '', unicodedata.normalize('NFC', c['text']))[:40].strip(' .') or '조리대사'
            name = f'{label}_{stamp(a)}-{stamp(b)}.mp3'
            if name in names:
                name = f'{Path(name).stem}_{order}.mp3'
            names.add(name)
            self.store.update(ident, message=f'음원 준비 중 · {order-1}/{len(chosen)}개 완료')
            try:
                bounded(a, b, info['duration'])
                with media.ENCODING:
                    media.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(a), '-i', str(source), '-t', str(b-a),
                               '-map', '0:a:0', '-vn', '-map_metadata', '-1', '-c:a', 'libmp3lame', '-b:a', '192k', str(directory/name)], 3600)
                record.update(status='finished', filename=name)
            except Exception as e:
                (directory/name).unlink(missing_ok=True)
                record.update(status='error', error=str(e))
            batch['clips'].append(record)
            self.store.update(ident, exports=state['exports'])
        self.manifest(directory, batch['clips'])
        batch['complete'] = True
        self.store.update(ident, exports=state['exports'], status='ready', message='음원 처리 완료 · 실패 항목은 개별 재시도할 수 있습니다.')

    def manifest(self, directory, records):
        (directory/'dialogues.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
        (directory/'dialogues.txt').write_text('\n\n'.join(f"{r['order']}. {r['text']}\n{stamp(r['start'])} – {stamp(r['end'])}\n{r.get('filename', r.get('error'))}" for r in records))
        with zipfile.ZipFile(directory/'dialogues.zip.tmp', 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in ['dialogues.json', 'dialogues.txt'] + [r['filename'] for r in records if r['status'] == 'finished']:
                archive.write(directory/name, name)
        os.replace(directory/'dialogues.zip.tmp', directory/'dialogues.zip')
