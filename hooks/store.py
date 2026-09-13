"""Atomic, disk-backed state. Only opaque IDs cross the HTTP boundary."""
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path


def uid():
    return uuid.uuid4().hex


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('잘못된 작업 식별자입니다.')
    return value


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()
        for name in ('jobs', 'uploads', 'assets', 'cache'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for path in (self.root / 'jobs').glob('*/state.json'):
            with self.lock:
                state = json.loads(path.read_text())
                if state.get('busy'):
                    state.update(busy=False, status='interrupted', message='서버가 재시작되었습니다. 중단된 단계를 다시 시도해주세요.')
                    self.write('jobs', state['id'], state)
        self.cleanup()

    def directory(self, kind, ident):
        return self.root / kind / valid_id(ident)

    def read(self, kind, ident):
        with self.lock:
            path = self.directory(kind, ident) / 'state.json'
            if not path.exists():
                raise FileNotFoundError('작업을 찾을 수 없습니다.')
            return json.loads(path.read_text(encoding='utf-8'))

    def write(self, kind, ident, data):
        with self.lock:
            if kind == 'jobs':
                data['updated'] = time.time()
            directory = self.directory(kind, ident)
            directory.mkdir(parents=True, exist_ok=True)
            temp = directory / (uid() + '.tmp')
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            os.replace(temp, directory / 'state.json')

    def update(self, ident, **changes):
        with self.lock:
            state = self.read('jobs', ident)
            state.update(changes, updated=time.time())
            self.write('jobs', ident, state)
            return state

    def cleanup(self):
        """Expire temporary media, never results or analysis. Called periodically."""
        import shutil
        cutoff = time.time() - 7 * 86400
        with self.lock:
            active_assets = set()
            for path in (self.root / 'jobs').glob('*/state.json'):
                state = json.loads(path.read_text())
                if state.get('busy') or state.get('last_media_use', state.get('updated', 0)) >= cutoff:
                    if state.get('asset_id'):
                        active_assets.add(state['asset_id'])
                    continue
                for name in ('source', 'preview'):
                    shutil.rmtree(path.parent / name, ignore_errors=True)
            for path in (self.root / 'uploads').glob('*/state.json'):
                state = json.loads(path.read_text())
                if state.get('updated', 0) < cutoff:
                    shutil.rmtree(path.parent, ignore_errors=True)
            for path in (self.root / 'assets').glob('*/state.json'):
                state = json.loads(path.read_text())
                if state['id'] not in active_assets and state.get('updated', 0) < cutoff:
                    (path.parent / 'video').unlink(missing_ok=True)
