"""Credentials file, OAuth flow and private upload jobs on the shared hooks store.

Google libraries are imported lazily so a failed install never breaks the rest of the app.
"""
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from hooks.store import valid_id, uid

os.environ.setdefault('OAUTHLIB_RELAX_TOKEN_SCOPE', '1')   # Google may reorder granted scopes.

SCOPES = ['https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube.readonly']
TOKEN_URI = 'https://oauth2.googleapis.com/token'
AUTH_URI = 'https://accounts.google.com/o/oauth2/auth'
REVOKE_URI = 'https://oauth2.googleapis.com/revoke'
FEATURE = 'youtube-upload'
CATEGORY_ID = '26'            # Howto & Style
CHUNK_SIZE = 8 * 1024 * 1024
TITLE_MAX = 100
DESCRIPTION_MAX_BYTES = 5000
REQUIRED_SCRIPT_VERSION = 15
ERROR_MESSAGES = {
    'quotaExceeded': '오늘 YouTube API 업로드 한도를 넘었습니다. 한국 시간 오후 5시(태평양 자정) 이후 다시 시도하거나 영상 없이 제출하세요.',
    'uploadLimitExceeded': '채널의 업로드 한도를 넘었습니다. 잠시 후 다시 시도하거나 영상 없이 제출하세요.',
    'forbidden': '이 채널에 업로드할 권한이 없습니다. 연결한 Google 계정과 채널을 확인해 주세요.',
    'invalid_grant': 'Google 연결이 만료되었습니다. 설정에서 Google 계정을 다시 연결해 주세요.',
    'invalidTitle': '유튜브가 제목을 거절했습니다. 제목을 고쳐 다시 시도하세요.',
    'invalidDescription': '유튜브가 설명을 거절했습니다. 설명을 고쳐 다시 시도하세요.',
}


class ApiError(Exception):
    """HTTP 경계로 그대로 전달되는 오류. status 와 code 는 응답에 실린다."""

    def __init__(self, message, status=400, code='bad_request', job=None):
        super().__init__(message)
        self.status, self.code, self.job = status, code, job


class Config:
    """app.py 가 주입하는 환경. 테스트는 이 객체의 속성을 바꾼다."""

    def __init__(self, credentials_file, load_settings, save_settings, settings_lock, sniff_image,
                 script_version, port, public_origin, thumb_max_bytes):
        self.credentials_file = Path(credentials_file)
        self.load_settings, self.save_settings, self.settings_lock = load_settings, save_settings, settings_lock
        self.sniff_image, self.script_version = sniff_image, script_version
        self.port, self.public_origin, self.thumb_max_bytes = port, public_origin, thumb_max_bytes
        self.lock = threading.Lock()

    def redirect_uri(self):
        return f'http://127.0.0.1:{self.port}/api/youtube/oauth/callback'

    def oauth_available(self):
        return not self.public_origin

    def audit_passed(self):
        with self.settings_lock:
            return bool(self.load_settings().get('youtube_audit_passed'))

    def set_audit_passed(self, value):
        with self.settings_lock:
            settings = self.load_settings()
            settings['youtube_audit_passed'] = bool(value)
            self.save_settings(settings)


def libraries_available():
    try:
        import googleapiclient.discovery  # noqa: F401
        import google_auth_oauthlib.flow  # noqa: F401
        return True
    except ImportError:
        return False


# ---- credentials file (0600) --------------------------------------------------
def read_credentials(cfg):
    try:
        data = json.loads(cfg.credentials_file.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def write_credentials(cfg, data):
    cfg.credentials_file.parent.mkdir(parents=True, exist_ok=True)
    temp = cfg.credentials_file.with_name(cfg.credentials_file.name + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp, cfg.credentials_file)
    os.chmod(cfg.credentials_file, 0o600)


def update_credentials(cfg, **changes):
    with cfg.lock:
        data = read_credentials(cfg)
        data.update(changes)
        write_credentials(cfg, data)
        return data


def parse_client_json(text):
    """Google Cloud 에서 내려받은 OAuth 클라이언트 JSON 에서 id/secret 을 꺼낸다."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as e:
        raise ApiError('OAuth 클라이언트 JSON 형식이 아닙니다.') from e
    if isinstance(data, dict) and 'installed' in data and 'web' not in data:
        raise ApiError('데스크톱 앱 유형입니다. Google Cloud 에서 "웹 애플리케이션" 유형의 클라이언트를 만들어 주세요.')
    web = data.get('web') if isinstance(data, dict) else None
    if not isinstance(web, dict) or not web.get('client_id') or not web.get('client_secret'):
        raise ApiError('JSON 에 web.client_id 와 web.client_secret 이 필요합니다.')
    return str(web['client_id']).strip(), str(web['client_secret']).strip()


def save_client(cfg, client_id, client_secret):
    client_id, client_secret = str(client_id or '').strip(), str(client_secret or '').strip()
    if not client_id.endswith('.apps.googleusercontent.com') or len(client_id) > 200 or not 1 <= len(client_secret) <= 200:
        raise ApiError('클라이언트 ID 또는 비밀번호 형식이 올바르지 않습니다.')
    with cfg.lock:
        current = read_credentials(cfg)
        if current.get('client_id') != client_id or current.get('client_secret') != client_secret:
            current = {'client_id': client_id, 'client_secret': client_secret}   # 새 클라이언트: 기존 연결 폐기
        write_credentials(cfg, current)


def clear_client(cfg):
    with cfg.lock:
        try:
            cfg.credentials_file.unlink()
        except FileNotFoundError:
            pass


def public_status(cfg, extra=None):
    creds = read_credentials(cfg)
    status = {
        'configured': bool(creds.get('client_id') and creds.get('client_secret')),
        'connected': bool(creds.get('refresh_token')),
        'channel_title': creds.get('channel_title') or '',
        'audit_passed': cfg.audit_passed(),
        'oauth_available': cfg.oauth_available(),
        'redirect_uri': cfg.redirect_uri(),
        'library_missing': not libraries_available(),
        'title_max': TITLE_MAX, 'description_max_bytes': DESCRIPTION_MAX_BYTES,
    }
    status.update(extra or {})
    return status


# ---- OAuth -----------------------------------------------------------------
def _flow(cfg):
    from google_auth_oauthlib.flow import Flow
    creds = read_credentials(cfg)
    if not creds.get('client_id') or not creds.get('client_secret'):
        raise ApiError('먼저 OAuth 클라이언트 JSON 을 저장해 주세요.', 409, 'youtube_not_configured')
    client = {'web': {'client_id': creds['client_id'], 'client_secret': creds['client_secret'],
                      'auth_uri': AUTH_URI, 'token_uri': TOKEN_URI}}
    return Flow.from_client_config(client, scopes=SCOPES, redirect_uri=cfg.redirect_uri())


def begin(cfg):
    if not cfg.oauth_available():
        raise ApiError('Google 연결은 로컬(127.0.0.1)에서 실행한 앱에서만 할 수 있습니다.', 409, 'oauth_unavailable')
    if not libraries_available():
        raise ApiError('Google 라이브러리가 없습니다. run.sh 를 다시 실행해 의존성을 설치해 주세요.', 503, 'library_missing')
    url, state = _flow(cfg).authorization_url(access_type='offline', prompt='consent', include_granted_scopes='false')
    return url, state


def complete(cfg, code):
    flow = _flow(cfg)
    flow.fetch_token(code=code)           # token endpoint is https; no insecure-transport override needed
    creds = flow.credentials
    if not creds.refresh_token:
        raise ApiError('Google 이 갱신 토큰을 주지 않았습니다. 동의 화면에서 접근을 다시 허용해 주세요.', 502, 'no_refresh_token')
    title, channel_id = '', ''
    try:
        response = build_client(creds).channels().list(part='snippet', mine=True).execute()
        item = (response.get('items') or [{}])[0]
        title, channel_id = item.get('snippet', {}).get('title', ''), item.get('id', '')
    except Exception as e:   # channel name is informational only
        _log('channels.list failed', e)
    update_credentials(cfg, refresh_token=creds.refresh_token, channel_title=title, channel_id=channel_id,
                       connected_at=datetime.now().isoformat(timespec='seconds'))
    return title


def credentials(cfg):
    from google.oauth2.credentials import Credentials
    data = read_credentials(cfg)
    if not data.get('refresh_token'):
        raise ApiError('Google 계정이 연결되지 않았습니다. 설정에서 먼저 연결해 주세요.', 409, 'youtube_not_connected')
    return Credentials(None, refresh_token=data['refresh_token'], token_uri=TOKEN_URI,
                       client_id=data['client_id'], client_secret=data['client_secret'], scopes=SCOPES)


def build_client(creds):
    """Worker threads call this once each; httplib2 is not thread-safe. Tests patch it."""
    from googleapiclient.discovery import build
    return build('youtube', 'v3', credentials=creds, cache_discovery=False, static_discovery=True)


def mark_disconnected(cfg):
    update_credentials(cfg, refresh_token=None)


def revoke(cfg):
    """Best effort. The refresh token is sent in the body, never in a query string."""
    data = read_credentials(cfg)
    token = data.get('refresh_token')
    if token:
        try:
            import urllib.request
            import urllib.parse
            req = urllib.request.Request(REVOKE_URI, data=urllib.parse.urlencode({'token': token}).encode(),
                                         headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as e:
            _log('revoke failed', e)
    update_credentials(cfg, refresh_token=None, channel_title='', channel_id='', connected_at=None)


def _log(prefix, exc):
    print(f'[youtube-upload] {prefix}: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)


# ---- metadata --------------------------------------------------------------
def validate_metadata(title, description):
    """YouTube: 제목 ≤100자, 설명 ≤5000바이트, 둘 다 < > 금지. 반환값은 (title, description, warnings)."""
    warnings = []
    title = ' '.join(str(title or '').split())
    description = str(description or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if any(ch in title + description for ch in '<>'):
        title, description = (s.replace('<', '〈').replace('>', '〉') for s in (title, description))
        warnings.append('제목·설명의 < > 는 유튜브에서 허용되지 않아 〈 〉 로 바꿨습니다.')
    if not 1 <= len(title) <= TITLE_MAX:
        raise ApiError(f'유튜브 제목은 1~{TITLE_MAX}자여야 합니다. (현재 {len(title)}자)', 400, 'bad_title')
    size = len(description.encode('utf-8'))
    if size > DESCRIPTION_MAX_BYTES:
        raise ApiError(f'유튜브 설명은 {DESCRIPTION_MAX_BYTES}바이트(한글 약 1,600자) 이하여야 합니다. (현재 {size}바이트)', 400, 'bad_description')
    return title, description, warnings


def describe_http_error(exc):
    """(code, message) from a googleapiclient HttpError without echoing Google's raw text to the client."""
    status = getattr(getattr(exc, 'resp', None), 'status', None)
    reason = ''
    try:
        details = getattr(exc, 'error_details', None) or []
        reason = next((d.get('reason') for d in details if isinstance(d, dict) and d.get('reason')), '')
        if not reason:
            body = json.loads(exc.content.decode('utf-8', 'replace'))
            reason = (body.get('error', {}).get('errors') or [{}])[0].get('reason', '')
    except Exception:
        pass
    if status == 401 or reason in ('authError', 'invalid_grant'):
        reason = 'invalid_grant'
    code = reason or f'http_{status}'
    return code, ERROR_MESSAGES.get(reason, f'유튜브 API 오류가 발생했습니다 ({code}). 잠시 후 다시 시도해 주세요.')


# ---- jobs ------------------------------------------------------------------
PUBLIC_KEYS = ('id', 'item_id', 'connection', 'title', 'description', 'name', 'size', 'busy', 'status', 'percent',
               'message', 'video_id', 'url', 'error_code', 'warnings', 'test_mode', 'has_thumbnail', 'created', 'updated')


class Service:
    """Wraps the shared hooks Service: same Store, same worker launcher, its own job feature."""

    def __init__(self, shared, cfg):
        self.shared, self.store, self.cfg = shared, shared.store, cfg

    def public(self, state):
        return {k: state.get(k) for k in PUBLIC_KEYS}

    def get(self, ident):
        state = self.store.read('jobs', valid_id(ident))
        if state.get('feature') != FEATURE:
            raise FileNotFoundError('유튜브 업로드 작업을 찾지 못했습니다.')
        return state

    def list_jobs(self, connection=None, item_id=None, limit=50):
        states = []
        with self.store.lock:
            for path in (self.store.root / 'jobs').glob('*/state.json'):
                try:
                    state = self.store.read('jobs', path.parent.name)
                except (FileNotFoundError, ValueError):
                    continue
                if state.get('feature') != FEATURE:
                    continue
                if connection and state.get('connection') != connection:
                    continue
                if item_id and state.get('item_id') != item_id:
                    continue
                states.append(state)
        states.sort(key=lambda s: s.get('created', 0), reverse=True)
        return [self.public(s) for s in states[:limit]]

    def find_job(self, item_id, connection):
        """The job the UI should adopt for this item: busy first, then the latest finished one."""
        jobs = self.list_jobs(connection, item_id)
        busy = [j for j in jobs if j['busy']]
        if busy:
            return busy[0]
        done = [j for j in jobs if j['status'] == 'done']
        if done:
            return done[0]
        return jobs[0] if jobs else None

    def uploads_today(self):
        midnight = datetime.now(ZoneInfo('America/Los_Angeles')).replace(hour=0, minute=0, second=0, microsecond=0)
        return sum(1 for j in self.list_jobs(limit=1000) if (j.get('created') or 0) >= midnight.timestamp() and j['status'] != 'error')

    def create(self, asset_id, item_id, connection, title, description, thumbnail=None, force=False):
        if not isinstance(item_id, str) or not item_id.strip() or len(item_id) > 200 or not isinstance(connection, str) or not connection:
            raise ApiError('항목 정보가 올바르지 않습니다.')
        credentials(self.cfg)   # raises youtube_not_connected
        test_mode = not self.cfg.audit_passed()
        if not test_mode:
            version = self.cfg.script_version()
            if not isinstance(version, int) or version < REQUIRED_SCRIPT_VERSION:
                raise ApiError(f'Apps Script 를 버전 {REQUIRED_SCRIPT_VERSION} 이상으로 배포해야 유튜브 링크를 시트에 기록할 수 있습니다. '
                               f'(현재 {version if version else "확인 불가"})', 409, 'upgrade_required')
        existing = self.find_job(item_id, connection)
        if existing and (existing['busy'] or (existing['status'] == 'done' and not force)):
            raise ApiError('이 항목에 이미 진행 중이거나 완료된 유튜브 업로드가 있습니다.', 409, 'job_exists', job=existing)
        title, description, warnings = validate_metadata(title, description)
        asset = self.store.read('assets', valid_id(asset_id))
        path = self.store.directory('assets', asset_id) / 'video'
        if not path.is_file():
            raise ApiError('업로드한 영상 파일을 찾을 수 없습니다. 파일을 다시 선택해 주세요.', 404, 'asset_missing')
        thumb_bytes = None
        if thumbnail:
            if len(thumbnail) > self.cfg.thumb_max_bytes or not self.cfg.sniff_image(thumbnail):
                raise ApiError('썸네일은 8 MB 이하의 JPG·PNG·WebP 여야 합니다.', 400, 'bad_thumbnail')
            thumb_bytes = thumbnail
        info = asset.get('info') or {}
        ident = uid()
        state = {'id': ident, 'feature': FEATURE, 'item_id': item_id, 'connection': connection, 'asset_id': asset_id,
                 'title': title, 'description': description, 'name': asset.get('name', ''), 'size': path.stat().st_size,
                 'mimetype': 'video/quicktime' if str(asset.get('name', '')).lower().endswith('.mov') else 'video/mp4',
                 'duration': info.get('duration'), 'width': info.get('width'), 'height': info.get('height'),
                 'busy': False, 'status': 'queued', 'percent': 0, 'message': '업로드 대기 중', 'video_id': None, 'url': None,
                 'error_code': None, 'warnings': warnings, 'test_mode': test_mode, 'has_thumbnail': bool(thumb_bytes),
                 'created': time.time(), 'updated': time.time(), 'last_media_use': time.time()}
        with self.store.lock:
            self.store.write('jobs', ident, state)
            if thumb_bytes:
                (self.store.directory('jobs', ident) / 'thumbnail.jpg').write_bytes(thumb_bytes)
        return self.start(ident)

    def start(self, ident):
        return self.shared.start(ident, 'uploading', lambda: self.run(ident))

    def retry(self, ident):
        state = self.get(ident)
        if state['busy']:
            return state
        if state['status'] not in ('error', 'interrupted'):
            raise ApiError('다시 시도할 수 있는 상태가 아닙니다.', 409, 'not_retryable')
        if not (self.store.directory('assets', state['asset_id']) / 'video').is_file():
            raise ApiError('영상 파일이 정리되었습니다. 파일을 다시 선택해 새로 업로드해 주세요.', 409, 'asset_missing')
        warnings = list(state.get('warnings') or [])
        if state.get('percent', 0) >= 99:
            note = '마무리 단계에서 중단되었습니다. YouTube Studio 에 영상이 이미 있으면 재시도하지 말고 Studio 의 영상을 사용하세요.'
            if note not in warnings:
                warnings.append(note)
        self.store.update(ident, warnings=warnings, error_code=None, percent=0)
        return self.start(ident)

    def job_for_submit(self, ident, item_id, connection):
        """The completed job whose video the sheet submit may reference, or None."""
        try:
            state = self.get(ident)
        except (FileNotFoundError, ValueError):
            return None
        if state['status'] != 'done' or not state.get('video_id') or state.get('item_id') != item_id or state.get('connection') != connection:
            return None
        return state

    # ---- worker --------------------------------------------------------------
    def run(self, ident):
        state = self.store.read('jobs', ident)
        try:
            from googleapiclient.errors import HttpError
            from googleapiclient.http import MediaFileUpload
            from google.auth.exceptions import RefreshError
        except ImportError:
            self.store.update(ident, status='error', error_code='library_missing',
                              message='Google 라이브러리가 없습니다. run.sh 를 다시 실행해 의존성을 설치해 주세요.')
            return
        try:
            path = self.store.directory('assets', state['asset_id']) / 'video'
            if not path.is_file():
                raise ApiError('영상 파일이 정리되었습니다. 파일을 다시 선택해 주세요.', 409, 'asset_missing')
            youtube = build_client(credentials(self.cfg))
            media = MediaFileUpload(str(path), mimetype=state['mimetype'], chunksize=CHUNK_SIZE, resumable=True)
            body = {'snippet': {'title': state['title'], 'description': state['description'], 'categoryId': CATEGORY_ID},
                    'status': {'privacyStatus': 'private', 'selfDeclaredMadeForKids': False}}
            request = youtube.videos().insert(part='snippet,status', body=body, media_body=media)
            self.store.update(ident, status='uploading', percent=0, message='유튜브에 업로드 중 0%', last_media_use=time.time())
            response = None
            while response is None:
                progress, response = request.next_chunk(num_retries=5)
                if progress:
                    percent = min(99, int(progress.progress() * 100))
                    self.store.update(ident, percent=percent, message=f'유튜브에 업로드 중 {percent}%',
                                      status='finalizing' if percent >= 99 else 'uploading')
            video_id = response['id']
            self.store.update(ident, video_id=video_id, url=f'https://youtu.be/{video_id}', percent=100,
                              status='thumbnail', message='썸네일 설정 중')
            warnings = list(state.get('warnings') or [])
            thumb = self.store.directory('jobs', ident) / 'thumbnail.jpg'
            if thumb.is_file():
                try:
                    youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumb), mimetype='image/jpeg')).execute(num_retries=2)
                    warnings.append('썸네일 설정을 요청했습니다. 반영 여부는 YouTube Studio 에서 확인하세요.')
                except HttpError as e:
                    code, _ = describe_http_error(e)
                    warnings.append('썸네일은 설정하지 못했습니다' + (' (채널 인증 또는 쇼츠 커스텀 썸네일 권한 필요)' if code == 'forbidden' else f' ({code})')
                                    + '. YouTube Studio 에서 직접 올려 주세요.')
                except Exception as e:
                    _log('thumbnails.set failed', e)
                    warnings.append('썸네일은 설정하지 못했습니다. YouTube Studio 에서 직접 올려 주세요.')
            try:
                path.unlink()
            except OSError:
                pass
            self.store.update(ident, status='done', message='유튜브 업로드 완료 (비공개)', warnings=warnings, percent=100)
        except RefreshError as e:
            _log('refresh failed', e)
            mark_disconnected(self.cfg)
            self.store.update(ident, status='error', error_code='invalid_grant', message=ERROR_MESSAGES['invalid_grant'])
        except HttpError as e:
            code, message = describe_http_error(e)
            _log(f'HttpError {code}', e)
            if code == 'invalid_grant':
                mark_disconnected(self.cfg)
            self.store.update(ident, status='error', error_code=code, message=message)
        except ApiError as e:
            self.store.update(ident, status='error', error_code=e.code, message=str(e))
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            self.store.update(ident, status='error', error_code='internal',
                              message='유튜브 업로드 중 오류가 발생했습니다. 다시 시도하거나 서버 로그를 확인하세요.')
