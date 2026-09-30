import json
from pathlib import Path
import shutil
import subprocess
import threading
import time

from hooks.store import Store, uid
from hooks.media import probe
from . import analysis, capcut

WORKER = threading.Semaphore(1)


class Cancelled(Exception):
    pass


class Service:
    def __init__(self, root, hooks_root):
        self.store = Store(root)
        self.hooks_root = Path(hooks_root)
        self.events = {}

    def create(self, data):
        opts = analysis.options(data.get('options', {}))
        ids = data.get('asset_ids', [])
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) > 40 or len(set(ids)) != len(ids):
            raise ValueError('중복 없이 최대 40개의 영상을 선택해주세요.')
        name = str(data.get('name') or '자동 편집').strip()[:100]
        project = data.get('project_id')
        draft_data = data.get('draft')
        if project and draft_data:
            raise ValueError('프로젝트 입력은 하나만 선택해주세요.')
        if not ids and not project and not draft_data:
            raise ValueError('영상 또는 캡컷 프로젝트를 입력해주세요.')
        source_project = None
        folder_assets = []
        if project:
            entry = capcut.projects().get(project)
            if not entry:
                raise ValueError('선택한 캡컷 프로젝트를 찾지 못했습니다.')
            source_project = entry['path']
            if data.get('folder_id'):
                folder = next((f for f in capcut.project_folders(entry) if f['id'] == data['folder_id']), None)
                if not folder:
                    raise ValueError('선택한 촬영영상 폴더를 찾지 못했습니다.')
                for material in folder['videos']:
                    path = Path(material['file_Path']).expanduser()
                    if not path.is_absolute() or not path.is_file() or path.suffix.lower() not in capcut.VIDEO_EXTENSIONS:
                        raise ValueError(f'촬영 원본을 찾지 못했습니다: {material.get("extra_info", "")}')
                    folder_assets.append({'name': path.name, 'path': str(path), 'info': probe(path)})
                source_project = None
            else:
                draft_data = capcut.read_draft(source_project)
        if draft_data is not None:
            if not isinstance(draft_data, dict) or not isinstance(draft_data.get('tracks'), list) or not isinstance(draft_data.get('materials'), dict):
                raise ValueError('올바른 draft_content.json 파일이 필요합니다.')
            capcut.validate_draft(draft_data)
        assets = folder_assets
        if folder_assets and ids:
            raise ValueError('프로젝트 보관함 폴더와 업로드 영상은 별도 작업으로 입력해주세요.')
        for ident in ids:
            from hooks.store import valid_id
            directory = self.hooks_root / 'assets' / valid_id(ident)
            if not (directory / 'state.json').exists() or not (directory / 'video').is_file():
                raise ValueError('업로드한 영상을 찾지 못했습니다. 다시 선택해주세요.')
            asset = json.loads((directory / 'state.json').read_text())
            assets.append({'name': asset['name'], 'path': str(directory / 'video'), 'info': asset['info']})
        ident = uid()
        state = dict(id=ident, name=name, status='queued', busy=True, progress=0,
                     message='분석 대기 중', options=opts, assets=assets, warnings=[], created=time.time())
        self.store.write('jobs', ident, state)
        directory = self.store.directory('jobs', ident)
        if draft_data is not None:
            (directory / 'input.json').write_text(json.dumps(draft_data, ensure_ascii=False))
        event = threading.Event()
        self.events[ident] = event
        thread = threading.Thread(target=self.work, args=(ident, source_project, event), daemon=True)
        thread.start()
        return state

    def run(self, args, event):
        # Log to disk so hours of FFmpeg output cannot block a PIPE or exhaust RAM.
        import tempfile
        with tempfile.TemporaryFile(mode='w+b') as log:
            process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=log)
            started = time.monotonic()
            try:
                while process.poll() is None:
                    if event.wait(.2):
                        raise Cancelled()
                    if time.monotonic() - started > 14400:
                        raise ValueError('분석 시간이 4시간을 초과했습니다. 영상을 나눠서 시도해주세요.')
                log.seek(0)
                text = log.read().decode(errors='replace')
                if process.returncode:
                    raise ValueError('영상 분석에 실패했습니다: ' + text[-500:])
                return text
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

    def work(self, ident, source_project, event):
        acquired = False
        try:
            while not acquired:
                if event.is_set():
                    raise Cancelled()
                acquired = WORKER.acquire(timeout=.2)
            self.process(ident, source_project, event)
        except Cancelled:
            self.store.update(ident, busy=False, status='cancelled', message='편집을 취소했습니다.')
        except Exception as e:
            self.store.update(ident, busy=False, status='failed', message=str(e) or '편집에 실패했습니다.')
        finally:
            if acquired:
                WORKER.release()
            self.events.pop(ident, None)

    def process(self, ident, source_project, event):
        directory = self.store.directory('jobs', ident)
        state = self.store.read('jobs', ident)
        opts = state['options']
        media = directory / 'media'
        media.mkdir(exist_ok=True)
        assets = []
        for i, asset in enumerate(state['assets']):
            if event.is_set():
                raise Cancelled()
            self.store.update(ident, message=f"소스 보관 중 · {asset['name']}", status='analyzing')
            path = media / f'{i:03d}{Path(asset["name"]).suffix.lower()}'
            shutil.copy2(asset['path'], path)
            assets.append(asset | {'path': str(path)})
        input_path = directory / 'input.json'
        if input_path.exists():
            draft = capcut.read_draft(input_path)
            # Uploaded companion media resolves basename references; ambiguity is an error.
            by_name = {}
            for asset in assets:
                by_name.setdefault(asset['name'], []).append(asset['path'])
            used = {s['material_id'] for t in draft['tracks'] for s in t['segments']}
            copied = {}
            for group in ('videos', 'audios'):
                for material in draft['materials'].get(group, []):
                    if material.get('id') not in used:
                        continue
                    raw = material.get('path')
                    if not raw:
                        continue
                    basename = raw.replace('\\', '/').rsplit('/', 1)[-1]
                    matches = by_name.get(basename, [])
                    if len(matches) > 1:
                        raise ValueError(f'같은 이름의 소스가 여러 개입니다: {basename}')
                    if matches:
                        material['path'] = matches[0]
                        continue
                    if source_project and '##_draftpath_placeholder_##' in raw:
                        raw = raw.replace('##_draftpath_placeholder_##', str(source_project.parent))
                    source = Path(raw).expanduser()
                    if not source.is_absolute() and source_project:
                        source = source_project.parent / source
                    if not source.is_absolute() or not source.is_file():
                        raise ValueError(f'프로젝트 원본을 찾지 못했습니다: {basename}. 같은 이름의 원본 영상을 함께 첨부해주세요.')
                    # Keep all original media durable; results never depend on temporary uploads.
                    if source.suffix.lower() not in capcut.VIDEO_EXTENSIONS | {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.png', '.jpg', '.jpeg', '.webp'}:
                        raise ValueError(f'지원하지 않는 소스 형식입니다: {basename}')
                    key = str(source.resolve())
                    if key not in copied:
                        target = media / (uid() + source.suffix.lower())
                        shutil.copy2(source, target)
                        copied[key] = str(target)
                    material['path'] = copied[key]
        else:
            draft = capcut.create_from_sources(directory, assets)
        capcut.validate_draft(draft)
        duration = max((s['target_timerange']['start'] + s['target_timerange']['duration']
                        for t in draft['tracks'] for s in t['segments']), default=0) / 1e6
        if duration <= 0:
            raise ValueError('프로젝트에 편집할 클립이 없습니다.')
        draft['duration'] = round(duration * 1e6)
        materials = {m['id']: m for k in ('videos', 'audios') for m in draft['materials'].get(k, [])}
        segments = [(t, s) for t in draft['tracks'] if t['type'] in ('video', 'audio') for s in t['segments']]
        active_audio, active_video, coverage, silent_coverage, cache, warnings = [], [], [], [], {}, []
        for index, (track, seg) in enumerate(segments):
            if event.is_set():
                raise Cancelled()
            t = seg['target_timerange']
            start, end = t['start'] / 1e6, (t['start'] + t['duration']) / 1e6
            material = materials.get(seg['material_id'])
            if not material or not material.get('path'):
                raise ValueError('로컬 원본이 없는 클립이 있습니다. 프로젝트를 영상으로 내보낸 뒤 입력해주세요.')
            is_video = track['type'] == 'video'
            visible = seg.get('visible', True) and not (track.get('attribute', 0) & 2)
            if is_video and visible:
                coverage.append([start, end])
            if material.get('type') == 'photo':
                # A still overlay should not make the moving main video appear frozen.
                continue
            path = material['path']
            if path not in cache:
                self.store.update(ident, message=f'무음·움직임 분석 중 · {index + 1}/{len(segments)}', progress=10 + round(75 * index / max(1, len(segments))))
                if is_video:
                    info = probe(path)
                else:
                    p = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-of', 'json', path], capture_output=True, text=True, timeout=60, check=True)
                    info = {'duration': float(json.loads(p.stdout)['format']['duration']), 'audio_codec': 'audio'}
                detect_opts = opts | {'freeze': opts['freeze'] and is_video}
                detected = analysis.detect(path, info['duration'], bool(info.get('audio_codec')), detect_opts, lambda args: self.run(args, event))
                cache[path] = (info, detected)
            info, detected = cache[path]
            src = seg.get('source_timerange') or {'start': 0, 'duration': t['duration']}
            rate = src['duration'] / t['duration']
            if rate <= 0:
                raise ValueError('클립 재생 속도가 올바르지 않습니다.')
            offset = src['start'] / 1e6
            def mapped(ranges):
                return analysis.intersect([[start + (a - offset) / rate, start + (b - offset) / rate] for a, b in ranges], [[start, end]])
            if is_video and visible:
                active_video += mapped(analysis.complement(detected['freeze'], info['duration']))
            audible = seg.get('volume', 1) > 0 and not (track.get('attribute', 0) & 1)
            if audible and info.get('audio_codec'):
                silent_coverage.append([start, end])
                active_audio += mapped(analysis.complement(detected['silence'], info['duration']))
            elif is_video and not info.get('audio_codec'):
                warnings.append('오디오 트랙이 없는 영상은 무음 제거 대상에서 제외했습니다.')
        if not coverage:
            raise ValueError('분석할 영상 트랙이 없습니다.')
        silence = analysis.intersect(analysis.complement(active_audio, duration), silent_coverage) if opts['silence'] else []
        freeze = analysis.intersect(analysis.complement(active_video, duration), coverage) if opts['freeze'] else []
        # Require the selected minimum duration on the timeline too (e.g. sped-up clips).
        silence = [r for r in silence if r[1] - r[0] >= opts['silence_seconds']]
        freeze = [r for r in freeze if r[1] - r[0] >= opts['freeze_seconds']]
        candidates = analysis.intersect(silence, freeze) if opts['mode'] == 'both' and opts['silence'] and opts['freeze'] else analysis.union(silence + freeze)
        cuts = analysis.union([[a + opts['padding'], b - opts['padding']] for a, b in analysis.intersect(candidates, coverage) if b - a > 2 * opts['padding'] + .04])
        remaining = duration - sum(b - a for a, b in cuts)
        if remaining < .1:
            raise ValueError('모든 구간이 제거 대상입니다. 기준을 낮추거나 컷 앞뒤 여유를 늘려주세요.')
        if event.is_set():
            raise Cancelled()
        self.store.update(ident, status='exporting', progress=92, message='캡컷 편집본 저장 중')
        edited = capcut.ripple(draft, cuts)
        capcut.save_result(directory, edited, state['name'])
        result = {'original_duration': duration, 'edited_duration': edited['duration'] / 1e6,
                  'removed_duration': duration - edited['duration'] / 1e6, 'cuts': [
                      {'start': a, 'end': b, 'reason': ' · '.join(label for label, ranges in [('무음', silence), ('정지', freeze)] if analysis.intersect([[a, b]], ranges))}
                      for a, b in cuts], 'segments': sum(len(t['segments']) for t in edited['tracks'] if t['type'] == 'video')}
        self.store.update(ident, status='finished', busy=False, progress=100,
                          message='편집본을 만들었습니다.' if cuts else '제거할 구간이 없어 원본 길이를 유지했습니다.',
                          result=result, warnings=sorted(set(warnings)))

    def public(self, state):
        return {k: v for k, v in state.items() if k != 'assets'}
