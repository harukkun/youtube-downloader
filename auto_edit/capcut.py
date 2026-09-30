"""Non-destructive draft generation, project discovery and native launching."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .analysis import complement, intersect

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm', '.m4v'}


def library_root():
    if os.environ.get('CAPCUT_DRAFTS_DIR'):
        return Path(os.environ['CAPCUT_DRAFTS_DIR']).expanduser().resolve()
    if sys.platform == 'darwin':
        return Path.home() / 'Movies/CapCut/User Data/Projects/com.lveditor.draft'
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'CapCut/User Data/Projects/com.lveditor.draft'


def projects():
    result = {}
    root = library_root()
    if root.is_dir():
        for folder in root.iterdir():
            if not folder.is_dir() or folder.name.startswith('.') or folder.is_symlink():
                continue
            candidates = [folder / 'draft_info.json', folder / 'draft_content.json']
            path = next((p for p in candidates if p.is_file()), None)
            if not path:
                continue
            ident = hashlib.sha256(str(folder).encode()).hexdigest()[:32]
            result[ident] = {'id': ident, 'name': folder.name, 'path': path}
    return result


def read_draft(path):
    if path.stat().st_size > 20 * 1024 ** 2:
        raise ValueError('프로젝트 JSON은 20 MB 이내여야 합니다.')
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, UnicodeError):
        raise ValueError('암호화되었거나 읽을 수 없는 캡컷 프로젝트입니다. 원본 영상 파일을 입력해주세요.')
    if not isinstance(data, dict) or not isinstance(data.get('tracks'), list) or not isinstance(data.get('materials'), dict):
        raise ValueError('draft_content.json 형식의 캡컷 프로젝트가 필요합니다.')
    return data


def validate_draft(data):
    """Refuse time-dependent constructs we cannot safely retime, before any write."""
    if not isinstance(data, dict) or not isinstance(data.get('materials'), dict) or not isinstance(data.get('tracks'), list):
        raise ValueError('올바른 캡컷 프로젝트 JSON이 필요합니다.')
    mats = data['materials']
    if not isinstance(data.get('canvas_config'), dict) or not all(isinstance(data['canvas_config'].get(k), int) and data['canvas_config'][k] > 0 for k in ('width', 'height')):
        raise ValueError('프로젝트의 화면 크기 정보가 올바르지 않습니다.')
    if not isinstance(data.get('keyframes') or {}, dict) or any(not isinstance(v, list) for v in mats.values()):
        raise ValueError('프로젝트의 소재 정보가 올바르지 않습니다.')
    if any(not isinstance(m, dict) for v in mats.values() for m in v):
        raise ValueError('프로젝트의 소재 정보가 올바르지 않습니다.')
    if mats.get('drafts') or mats.get('transitions') or any(s.get('curve_speed') for s in mats.get('speeds', [])):
        raise ValueError('복합 클립·전환·곡선 변속이 있는 프로젝트는 먼저 해당 구간을 영상으로 내보낸 뒤 입력해주세요.')
    if any((data.get('keyframes') or {}).values()) or data.get('keyframe_graph_list'):
        raise ValueError('키프레임이 있는 프로젝트는 영상으로 내보낸 뒤 입력해주세요.')
    for track in data['tracks']:
        if not isinstance(track, dict) or not isinstance(track.get('segments'), list):
            raise ValueError('프로젝트의 트랙 정보가 올바르지 않습니다.')
        if track.get('type') not in ('video', 'audio', 'text', 'sticker', 'effect', 'filter'):
            raise ValueError('지원하지 않는 트랙이 있습니다. 해당 프로젝트를 영상으로 내보낸 뒤 입력해주세요.')
        for seg in track.get('segments', []):
            if not isinstance(seg, dict) or not isinstance(seg.get('material_id'), str):
                raise ValueError('프로젝트의 클립 정보가 올바르지 않습니다.')
            source = seg.get('source_timerange')
            if source is not None and (not isinstance(source, dict) or any(type(source.get(k)) is not int for k in ('start', 'duration')) or source['start'] < 0 or source['duration'] <= 0):
                raise ValueError('클립의 원본 시간 정보가 올바르지 않습니다.')
            if seg.get('reverse') or seg.get('common_keyframes') or seg.get('keyframe_refs') or seg.get('is_loop'):
                raise ValueError('역재생·반복·키프레임 클립은 영상으로 내보낸 뒤 입력해주세요.')
            t = seg.get('target_timerange') or {}
            if not isinstance(t.get('start'), int) or not isinstance(t.get('duration'), int) or t['start'] < 0 or t['duration'] <= 0:
                raise ValueError('프로젝트의 클립 시간 정보가 올바르지 않습니다.')
    # Imported animations/fades have local time ranges that would be invalid after cuts.
    if any(a.get('animations') for a in mats.get('material_animations', [])) or mats.get('audio_fades'):
        raise ValueError('애니메이션·페이드가 있는 프로젝트는 영상으로 내보낸 뒤 입력해주세요.')


def ripple(data, cuts):
    """Split every track using the same global keep windows; preserve material properties."""
    result = copy.deepcopy(data)
    duration = data['duration'] / 1e6
    keep = complement(cuts, duration)
    for track in result['tracks']:
        segments = []
        for original in track['segments']:
            t = original['target_timerange']
            start, end = t['start'] / 1e6, (t['start'] + t['duration']) / 1e6
            for lo, hi in intersect(keep, [[start, end]]):
                seg = copy.deepcopy(original)
                seg['id'] = str(uuid.uuid4()).upper()
                removed = sum(max(0, min(lo, b) - a) for a, b in cuts if a < lo)
                seg['target_timerange'] = {'start': round((lo - removed) * 1e6), 'duration': round((hi - lo) * 1e6)}
                source = original.get('source_timerange')
                if source:
                    rate = source['duration'] / t['duration']
                    seg['source_timerange'] = {'start': source['start'] + round((lo - start) * rate * 1e6),
                                              'duration': round((hi - lo) * rate * 1e6)}
                seg['render_timerange'] = {'start': 0, 'duration': 0}
                seg['group_id'] = ''
                segments.append(seg)
        track['segments'] = segments
    result['duration'] = round(sum(b - a for a, b in keep) * 1e6)
    result['id'] = str(uuid.uuid4()).upper()
    result['relationships'] = []
    result['group_container'] = None
    result['time_marks'] = None
    return result


def pycapcut():
    import pycapcut as cc
    # pyCapCut 0.0.3 checks cls.__dict__ for annotations, which are lazy on 3.14.
    if sys.version_info >= (3, 14):
        from typing import get_type_hints
        def assign(obj, attrs, data):
            hints = get_type_hints(type(obj))
            for attr in attrs:
                typ = hints[attr]
                setattr(obj, attr, typ.import_json(data[attr]) if hasattr(typ, 'import_json') else typ(data[attr]))
        cc.util.assign_attr_with_json = assign
    return cc


def create_from_sources(directory, assets):
    cc = pycapcut()
    first = assets[0]['info']
    script = cc.DraftFolder(str(directory)).create_draft('draft', first['width'], first['height'])
    script.add_track(cc.TrackType.video)
    cursor = 0
    for asset in assets:
        material = cc.VideoMaterial(str(asset['path']))
        script.add_segment(cc.VideoSegment(material, cc.Timerange(cursor, material.duration)))
        cursor += material.duration
    script.save()
    return read_draft(directory / 'draft/draft_content.json')


def save_result(directory, data, name):
    cc = pycapcut()
    draft = directory / 'draft'
    if not draft.exists():
        cc.DraftFolder(str(directory)).create_draft('draft', data['canvas_config']['width'], data['canvas_config']['height'])
    data['name'] = name
    path = draft / 'draft_content.json'
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    (draft / 'draft_info.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    # Validate through pyCapCut's template loader without reserializing unknown native fields.
    cc.ScriptFile.load_template(str(path))
    meta_path = draft / 'draft_meta_info.json'
    meta = json.loads(meta_path.read_text())
    meta.update(draft_id=data['id'], draft_name=name, draft_fold_path=str(draft), draft_root_path=str(directory),
                tm_duration=data['duration'], tm_draft_create=int(time.time() * 1e6), tm_draft_modified=int(time.time() * 1e6))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    return draft


def launch(draft):
    """Install a durable working copy and activate CapCut without claiming it opened.

    CapCut 9.3 on macOS ignores --draft_path for its main process. Its web-call
    deep link only opens cloud-matched drafts, so do not send local jobs there.
    A successful OS launch is not proof that the requested timeline is open.
    """
    if sys.platform == 'darwin':
        app = Path('/Applications/CapCut.app')
        if not app.exists():
            app = Path.home() / 'Applications/CapCut.app'
        if not app.exists():
            raise ValueError('이 Mac에 CapCut을 설치한 뒤 다시 시도해주세요.')
        destination = install_result(draft)
        process = subprocess.run(['open', '-a', str(app)], capture_output=True, text=True, timeout=20)
        if process.returncode:
            raise ValueError('캡컷 실행에 실패했습니다: ' + process.stderr[-300:])
    elif sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', '')) / 'CapCut/Apps'
        apps = sorted(base.glob('*/CapCut.exe'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not apps:
            raise ValueError('CapCut 설치 경로를 찾지 못했습니다.')
        destination = install_result(draft)
        subprocess.Popen([str(apps[0])])
    else:
        raise ValueError('캡컷에서 열기는 Mac 또는 Windows 서버에서 사용할 수 있습니다.')
    return {'opened': False, 'project_name': destination.name,
            'message': f'캡컷에 편집본을 등록했습니다. 현재 버전에서는 홈의 “{destination.name}” 프로젝트를 선택해주세요. 자동으로 타임라인을 여는 연결은 아직 지원되지 않습니다.'}


def install_result(draft):
    """Publish a separate editable copy to CapCut's watched local draft folder."""
    import shutil
    root = library_root()
    root.mkdir(parents=True, exist_ok=True)
    data = read_draft(draft / 'draft_content.json')
    # A stable name makes repeated Open clicks return to the user's edited copy.
    folder_name = ''.join(c for c in data.get('name', '자동 편집') if c not in '/\\:*?"<>|' and ord(c) >= 32)[:80].strip() + ' · ' + data['id'][:8]
    destination = root / folder_name
    if destination.is_dir():
        return destination
    staging = root / ('.autocut-' + uuid.uuid4().hex)
    try:
        shutil.copytree(draft, staging)
        meta_path = staging / 'draft_meta_info.json'
        meta = json.loads(meta_path.read_text())
        meta.update(draft_fold_path=str(destination), draft_root_path=str(root))
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
        os.rename(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination


def project_folders(project):
    """Read CapCut media-bin folders independently from timeline materials."""
    root = project['path'].parent
    virtual = root / 'draft_virtual_store.json'
    meta = root / 'draft_meta_info.json'
    if not virtual.is_file() or not meta.is_file():
        return []
    store = json.loads(virtual.read_text(encoding='utf-8'))
    info = json.loads(meta.read_text(encoding='utf-8'))
    groups = {g['type']: g.get('value', []) for g in store.get('draft_virtual_store', [])}
    materials = {m['id']: m for g in info.get('draft_materials', []) for m in g.get('value', [])}
    result = []
    for folder in groups.get(0, []):
        if not folder.get('id'):
            continue
        descendants = {folder['id']}
        while True:
            added = {r['child_id'] for r in groups.get(1, []) if r.get('parent_id') in descendants}
            if added <= descendants:
                break
            descendants |= added
        videos = [m for ident, m in materials.items() if ident in descendants and m.get('metetype') == 'video']
        videos.sort(key=lambda m: (m.get('import_time_ms', 0), m.get('extra_info', '')))
        if videos:
            result.append({'id': folder['id'], 'name': folder['display_name'], 'count': len(videos), 'videos': videos})
    return result
