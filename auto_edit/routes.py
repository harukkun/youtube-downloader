import os
import subprocess
from functools import wraps
from pathlib import Path
import threading
from urllib.parse import urlsplit
from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from .service import Service
from . import capcut


def install(app):
    bp = Blueprint('auto_edit', __name__)
    services, lock = {}, threading.Lock()

    def service():
        root = str(current_app.config.get('AUTO_EDIT_DATA_DIR') or os.environ.get('AUTO_EDIT_DATA_DIR') or Path.home() / '.youtube-downloader/auto-edit')
        hooks = current_app.config.get('HOOKS_DATA_DIR') or os.environ.get('HOOKS_DATA_DIR') or Path.home() / '.youtube-downloader/hooks'
        with lock:
            if root not in services:
                services[root] = Service(root, hooks)
            return services[root]

    @bp.before_request
    def same_origin():
        if request.method == 'POST':
            origin = request.headers.get('Origin')
            if (origin and urlsplit(origin).netloc != request.host) or request.headers.get('Sec-Fetch-Site') == 'cross-site':
                return jsonify(error='앱 페이지에서 다시 시도해주세요.'), 403

    def guarded(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except FileNotFoundError as e:
                return jsonify(error=str(e)), 404
            except (ValueError, TypeError, KeyError) as e:
                return jsonify(error=str(e)), 400
            except subprocess.TimeoutExpired:
                return jsonify(error='캡컷 실행 응답이 늦습니다. 캡컷을 직접 실행한 뒤 다시 시도해주세요.'), 504
            except OSError as e:
                return jsonify(error='파일 접근 또는 프로그램 실행에 실패했습니다: ' + str(e)), 400
        return wrapper

    @bp.get('/auto-edit')
    def page():
        return render_template('auto_edit.html')

    @bp.get('/api/auto-edit/projects')
    @guarded
    def projects():
        return jsonify(projects=[{k: v for k, v in p.items() if k != 'path'} for p in capcut.projects().values()])

    @bp.get('/api/auto-edit/projects/<ident>/folders')
    @guarded
    def folders(ident):
        entry = capcut.projects().get(ident)
        if not entry:
            raise ValueError('프로젝트를 찾지 못했습니다.')
        return jsonify(folders=[{k: v for k, v in f.items() if k != 'videos'} for f in capcut.project_folders(entry)])

    @bp.get('/api/auto-edit/jobs')
    @guarded
    def history():
        s = service()
        states = [s.public(s.store.read('jobs', p.parent.name)) for p in (s.store.root / 'jobs').glob('*/state.json')]
        return jsonify(jobs=sorted(states, key=lambda s: s['created'], reverse=True)[:50])

    @bp.post('/api/auto-edit/jobs')
    @guarded
    def create():
        if request.content_length and request.content_length > 10 * 1024 ** 2:
            return jsonify(error='프로젝트 JSON은 10 MB 이내여야 합니다.'), 413
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('올바른 편집 입력이 필요합니다.')
        s = service()
        return jsonify(s.public(s.create(data))), 202

    @bp.get('/api/auto-edit/jobs/<ident>')
    @guarded
    def get(ident):
        s = service()
        return jsonify(s.public(s.store.read('jobs', ident)))

    @bp.post('/api/auto-edit/jobs/<ident>/cancel')
    @guarded
    def cancel(ident):
        s = service()
        state = s.store.read('jobs', ident)
        event = s.events.get(ident)
        if state['busy'] and event:
            event.set()
        return jsonify(ok=True)

    @bp.post('/api/auto-edit/jobs/<ident>/open')
    @guarded
    def open_draft(ident):
        s = service()
        state = s.store.read('jobs', ident)
        if state['status'] != 'finished':
            raise ValueError('완료된 편집본만 열 수 있습니다.')
        result = capcut.launch(s.store.directory('jobs', ident) / 'draft')
        return jsonify(ok=True, **result)

    @bp.get('/api/auto-edit/jobs/<ident>/draft')
    @guarded
    def download(ident):
        s = service()
        state = s.store.read('jobs', ident)
        if state['status'] != 'finished':
            raise ValueError('완료된 편집본만 저장할 수 있습니다.')
        return send_file(s.store.directory('jobs', ident) / 'draft/draft_content.json', as_attachment=True, download_name='draft_content.json')

    app.register_blueprint(bp)
