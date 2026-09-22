"""/api/youtube/*: OAuth connection, settings and upload jobs. Video bytes travel through /api/hooks/uploads."""
import json
from functools import wraps
from pathlib import Path
from flask import Blueprint, current_app, jsonify, redirect, request, session
from . import service as svc


def install(app, load_settings, save_settings, settings_lock, sniff_image, script_version, port, public_origin, thumb_max_bytes):
    bp = Blueprint('youtube_upload', __name__, url_prefix='/api/youtube')
    cfg = svc.Config(app.config.get('YOUTUBE_CREDENTIALS_FILE') or Path.home() / '.youtube-downloader' / 'youtube_credentials.json',
                     load_settings, save_settings, settings_lock, sniff_image, script_version, port, public_origin, thumb_max_bytes)
    app.extensions['youtube_upload_config'] = cfg

    def service():
        return svc.Service(current_app.extensions['hooks_service'](), cfg)

    def guarded(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except svc.ApiError as e:
                body = {'error': str(e), 'code': e.code}
                if e.job is not None:
                    body['job'] = e.job
                return jsonify(body), e.status
            except FileNotFoundError as e:
                return jsonify(error=str(e), code='not_found'), 404
            except (ValueError, KeyError, TypeError) as e:
                return jsonify(error=str(e), code='bad_request'), 400
        return wrapped

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('JSON 객체가 필요합니다.')
        return data

    def status_payload(s):
        extra = {'uploads_today': s.uploads_today(), 'chunk_size': svc.CHUNK_SIZE}
        if request.args.get('script') == '1':
            extra['apps_script_version'] = cfg.script_version()
        return svc.public_status(cfg, extra)

    @bp.get('/status')
    @guarded
    def status():
        return jsonify(status_payload(service()))

    @bp.post('/credentials')
    @guarded
    def credentials():
        data = body()
        s = service()
        if any(j['busy'] for j in s.list_jobs()):
            raise svc.ApiError('진행 중인 업로드가 끝난 뒤 연결을 변경해 주세요.', 409, 'busy')
        if data.get('client_json'):
            client_id, client_secret = svc.parse_client_json(data['client_json'])
        else:
            client_id, client_secret = data.get('client_id', ''), data.get('client_secret', '')
        if not client_id and not client_secret:
            svc.revoke(cfg)
            svc.clear_client(cfg)
        else:
            svc.save_client(cfg, client_id, client_secret)
        return jsonify(ok=True, **status_payload(s))

    @bp.post('/settings')
    @guarded
    def settings():
        data = body()
        if 'audit_passed' in data:
            if type(data['audit_passed']) is not bool:
                raise ValueError('audit_passed 는 true 또는 false 여야 합니다.')
            cfg.set_audit_passed(data['audit_passed'])
        return jsonify(ok=True, **status_payload(service()))

    @bp.post('/oauth/start')
    @guarded
    def oauth_start():
        url, state = svc.begin(cfg)
        session['yt_oauth_state'] = state
        return jsonify(url=url)

    @bp.get('/oauth/callback')
    def oauth_callback():
        expected = session.pop('yt_oauth_state', None)
        state, code, error = request.args.get('state', ''), request.args.get('code', ''), request.args.get('error', '')
        if error:
            return redirect('/upload-process?youtube=error&code=denied')
        if not expected or not state or state != expected or not code:
            return redirect('/upload-process?youtube=error&code=state')
        try:
            svc.complete(cfg, code)
        except svc.ApiError as e:
            return redirect(f'/upload-process?youtube=error&code={e.code}')
        except Exception as e:   # never echo Google's error text into the URL
            svc._log('oauth callback failed', e)
            return redirect('/upload-process?youtube=error&code=exchange')
        return redirect('/upload-process?youtube=connected')

    @bp.post('/disconnect')
    @guarded
    def disconnect():
        s = service()
        if any(j['busy'] for j in s.list_jobs()):
            raise svc.ApiError('진행 중인 업로드가 끝난 뒤 연결을 해제해 주세요.', 409, 'busy')
        svc.revoke(cfg)
        return jsonify(ok=True, **status_payload(s))

    @bp.get('/jobs')
    @guarded
    def jobs():
        s = service()
        item_id, connection = request.args.get('item_id') or None, request.args.get('connection') or None
        if item_id:
            job = s.find_job(item_id, connection)
            return jsonify(job=job, jobs=[job] if job else [])
        return jsonify(jobs=s.list_jobs(connection))

    @bp.post('/jobs')
    @guarded
    def create_job():
        try:
            payload = json.loads(request.form.get('payload', ''))
        except (TypeError, ValueError):
            payload = None
        allowed = {'asset_id', 'item_id', 'connection', 'title', 'description', 'force'}
        if not isinstance(payload, dict) or not set(payload) <= allowed or not {'asset_id', 'item_id', 'connection', 'title', 'description'} <= set(payload):
            raise ValueError('요청 형식이 올바르지 않습니다.')
        if any(not isinstance(payload[k], str) for k in ('asset_id', 'item_id', 'connection', 'title', 'description')):
            raise ValueError('요청 형식이 올바르지 않습니다.')
        thumbnail = None
        f = request.files.get('thumbnail')
        if f is not None:
            thumbnail = f.read(thumb_max_bytes + 1)
        s = service()
        state = s.create(payload['asset_id'], payload['item_id'], payload['connection'], payload['title'], payload['description'],
                         thumbnail=thumbnail, force=payload.get('force') is True)
        return jsonify(s.public(state)), 201

    @bp.get('/jobs/<ident>')
    @guarded
    def get_job(ident):
        s = service()
        return jsonify(s.public(s.get(ident)))

    @bp.post('/jobs/<ident>/retry')
    @guarded
    def retry_job(ident):
        s = service()
        return jsonify(s.public(s.retry(ident)))

    app.register_blueprint(bp)
    return cfg
