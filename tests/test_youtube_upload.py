"""YouTube upload: credentials file, OAuth endpoints, job lifecycle and access control (offline; Google client is faked)."""
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app as appmod
from youtube_upload import service as svc
from tests.hooks_fixture import make_video

H = {'Origin': 'http://localhost'}
CLIENT = json.dumps({'web': {'client_id': 'abc.apps.googleusercontent.com', 'client_secret': 'secret-value'}})


class FakeHttpError(Exception):
    """Shape-compatible with googleapiclient.errors.HttpError for describe_http_error."""

    def __init__(self, status, reason):
        super().__init__(f'<HttpError {status}>')
        self.resp = type('Resp', (), {'status': status})()
        self.error_details = [{'reason': reason}]
        self.content = json.dumps({'error': {'errors': [{'reason': reason}]}}).encode()


class FakeYouTube:
    """videos().insert().next_chunk() yields progress then a response; thumbnails().set() is controllable."""

    def __init__(self, control):
        self.control = control
        control.setdefault('inserts', 0)
        control.setdefault('chunks', 0)
        control.setdefault('thumbnails', 0)

    def videos(self):
        return self

    def thumbnails(self):
        return self

    def channels(self):
        return self

    def list(self, **kw):
        return type('Req', (), {'execute': lambda s, **k: {'items': [{'id': 'UC1', 'snippet': {'title': '테스트 채널'}}]}})()

    def insert(self, **kw):
        fake, control = self, self.control
        control['inserts'] += 1
        control['last_insert'] = kw

        class Request:
            def __init__(self):
                self.calls = 0

            def next_chunk(self, num_retries=0):
                control['chunks'] += 1
                self.calls += 1
                if control.get('fail'):
                    raise FakeHttpError(*control['fail'])
                if self.calls == 1:
                    return type('P', (), {'progress': lambda s: .5})(), None
                return None, {'id': control.get('video_id', 'fixture0001')}
        return Request()

    def set(self, **kw):
        self.control['thumbnails'] += 1
        control = self.control

        class Req:
            def execute(self, num_retries=0):
                if control.get('thumb_fail'):
                    raise FakeHttpError(403, 'forbidden')
                return {}
        return Req()


class YouTubeUploadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.video = Path(cls.temp.name) / 'clock.mp4'
        make_video(cls.video)
        cls.bytes = cls.video.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        appmod.app.config['HOOKS_DATA_DIR'] = self.tmp.name
        self.cfg = appmod.app.extensions['youtube_upload_config']
        self.settings = {}
        patches = [patch.object(self.cfg, 'credentials_file', Path(self.tmp.name) / 'creds.json'),
                   patch.object(self.cfg, 'load_settings', lambda: dict(self.settings)),
                   patch.object(self.cfg, 'save_settings', lambda d: self.settings.update(d)),
                   patch.object(self.cfg, 'script_version', lambda: 15),
                   patch.object(self.cfg, 'public_origin', '')]
        for p in patches:
            p.start(); self.addCleanup(p.stop)
        self.control = {}
        p = patch.object(svc, 'build_client', lambda creds: FakeYouTube(self.control))
        p.start(); self.addCleanup(p.stop)
        # googleapiclient's HttpError is what the worker catches; make it our fake so no network types are needed.
        import googleapiclient.errors
        p = patch.object(googleapiclient.errors, 'HttpError', FakeHttpError)
        p.start(); self.addCleanup(p.stop)
        self.client = appmod.app.test_client()

    # ---- helpers ------------------------------------------------------------
    def connect(self):
        svc.write_credentials(self.cfg, {'client_id': 'abc.apps.googleusercontent.com', 'client_secret': 's',
                                         'refresh_token': 'refresh', 'channel_title': '테스트 채널'})

    def upload_asset(self):
        r = self.client.post('/api/hooks/uploads', json={'name': 'clock.mp4', 'size': len(self.bytes)}).get_json()
        header = {'X-Chunk-SHA256': hashlib.sha256(self.bytes).hexdigest(), 'Content-Type': 'application/octet-stream'}
        self.assertEqual(self.client.put(f"/api/hooks/uploads/{r['id']}/chunks/0", data=self.bytes, headers=header).status_code, 200)
        return self.client.post(f"/api/hooks/uploads/{r['id']}/complete").get_json()['asset_id']

    def create(self, asset=None, title='제목', description='설명', item_id='item-1', thumbnail=None, force=False):
        payload = {'asset_id': asset or self.upload_asset(), 'item_id': item_id, 'connection': 'conn', 'title': title, 'description': description}
        if force:
            payload['force'] = True
        data = {'payload': json.dumps(payload)}
        if thumbnail is not None:
            import io
            data['thumbnail'] = (io.BytesIO(thumbnail), 'thumb.jpg')
        r = self.client.post('/api/youtube/jobs', data=data, headers=H)
        r.request.close()
        return r

    def wait(self, ident):
        for _ in range(200):
            state = self.client.get(f'/api/youtube/jobs/{ident}').get_json()
            if not state['busy']:
                return state
            time.sleep(.02)
        self.fail('job timeout')

    def asset_video(self, asset_id):
        return Path(self.tmp.name) / 'assets' / asset_id / 'video'

    # ---- metadata ------------------------------------------------------------
    def test_metadata_limits_and_bracket_replacement(self):
        self.assertEqual(svc.validate_metadata('  두  칸 ', 'a\r\nb')[:2], ('두 칸', 'a\nb'))
        title, desc, warnings = svc.validate_metadata('<제목>', '설명 <b>')
        self.assertEqual((title, desc), ('〈제목〉', '설명 〈b〉'))
        self.assertEqual(len(warnings), 1)
        with self.assertRaises(svc.ApiError) as ctx:
            svc.validate_metadata('가' * 101, '')
        self.assertEqual(ctx.exception.code, 'bad_title')
        with self.assertRaises(svc.ApiError):
            svc.validate_metadata('', '')
        svc.validate_metadata('t', '한' * 1666)
        with self.assertRaises(svc.ApiError) as ctx:
            svc.validate_metadata('t', '한' * 1667)
        self.assertEqual(ctx.exception.code, 'bad_description')

    def test_http_error_mapping_never_echoes_raw_text(self):
        code, message = svc.describe_http_error(FakeHttpError(403, 'quotaExceeded'))
        self.assertEqual(code, 'quotaExceeded')
        self.assertIn('한도', message)
        code, _ = svc.describe_http_error(FakeHttpError(401, 'authError'))
        self.assertEqual(code, 'invalid_grant')
        code, message = svc.describe_http_error(FakeHttpError(500, ''))
        self.assertEqual(code, 'http_500')
        self.assertNotIn('HttpError', message)

    # ---- credentials & OAuth -------------------------------------------------
    def test_credentials_file_is_private_and_secret_never_leaves(self):
        r = self.client.post('/api/youtube/credentials', json={'client_json': CLIENT}, headers=H)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(r.get_json()['configured'])
        self.assertFalse(r.get_json()['connected'])
        self.assertEqual(oct(os.stat(self.cfg.credentials_file).st_mode)[-3:], '600')
        text = self.client.get('/api/youtube/status').get_data(as_text=True)
        self.assertNotIn('secret-value', text)
        self.assertNotIn('client_secret', text)
        self.assertEqual(self.client.post('/api/youtube/credentials', json={'client_json': json.dumps({'installed': {}})}, headers=H).status_code, 400)
        # Replacing the client discards an existing connection.
        self.connect()
        self.client.post('/api/youtube/credentials', json={'client_json': CLIENT}, headers=H)
        self.assertFalse(self.client.get('/api/youtube/status').get_json()['connected'])
        # Clearing removes the file.
        self.client.post('/api/youtube/credentials', json={}, headers=H)
        self.assertFalse(self.cfg.credentials_file.exists())

    def test_oauth_start_uses_session_state_and_callback_validates(self):
        self.assertEqual(self.client.post('/api/youtube/oauth/start', json={}, headers=H).status_code, 409)
        self.client.post('/api/youtube/credentials', json={'client_json': CLIENT}, headers=H)
        r = self.client.post('/api/youtube/oauth/start', json={}, headers=H)
        self.assertEqual(r.status_code, 200, r.get_json())
        url = r.get_json()['url']
        self.assertIn('client_id=abc.apps.googleusercontent.com', url)
        self.assertIn('access_type=offline', url)
        self.assertIn('redirect_uri=http%3A%2F%2F127.0.0.1%3A', url)
        self.assertNotIn('secret-value', url)
        state = url.split('state=')[1].split('&')[0]
        self.assertEqual(self.client.get('/api/youtube/oauth/callback?state=wrong&code=c').location, '/upload-process?youtube=error&code=state')
        # The state was consumed by the failed attempt; start again and complete with a patched token exchange.
        url = self.client.post('/api/youtube/oauth/start', json={}, headers=H).get_json()['url']
        state = url.split('state=')[1].split('&')[0]

        class Creds:
            refresh_token = 'new-refresh'

        class FakeFlow:
            credentials = Creds()

            def fetch_token(self, code):
                assert code == 'the-code'

        with patch.object(svc, '_flow', lambda cfg: FakeFlow()):
            r = self.client.get(f'/api/youtube/oauth/callback?state={state}&code=the-code')
        self.assertEqual(r.location, '/upload-process?youtube=connected')
        status = self.client.get('/api/youtube/status').get_json()
        self.assertTrue(status['connected'])
        self.assertEqual(status['channel_title'], '테스트 채널')
        self.assertEqual(svc.read_credentials(self.cfg)['refresh_token'], 'new-refresh')
        self.assertEqual(self.client.get('/api/youtube/oauth/callback?error=access_denied').location, '/upload-process?youtube=error&code=denied')

    def test_disconnect_and_public_mode(self):
        self.connect()
        with patch.object(svc, 'revoke', lambda cfg: svc.update_credentials(cfg, refresh_token=None, channel_title='')):
            r = self.client.post('/api/youtube/disconnect', json={}, headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()['connected'])
        self.assertTrue(r.get_json()['configured'])
        with patch.object(self.cfg, 'public_origin', 'https://1.2.3.4'):
            status = self.client.get('/api/youtube/status').get_json()
            self.assertFalse(status['oauth_available'])
            self.assertEqual(self.client.post('/api/youtube/oauth/start', json={}, headers=H).get_json()['code'], 'oauth_unavailable')

    def test_settings_toggle(self):
        r = self.client.post('/api/youtube/settings', json={'audit_passed': True}, headers=H)
        self.assertTrue(r.get_json()['audit_passed'])
        self.assertTrue(self.settings['youtube_audit_passed'])
        self.assertEqual(self.client.post('/api/youtube/settings', json={'audit_passed': 'yes'}, headers=H).status_code, 400)

    # ---- jobs ------------------------------------------------------------------
    def test_job_requires_connection_then_uploads_private_and_deletes_source(self):
        self.assertEqual(self.create().get_json()['code'], 'youtube_not_connected')
        self.connect()
        asset = self.upload_asset()
        r = self.create(asset=asset, title='제목 <1>', description='설명', thumbnail=b'\xff\xd8\xffjpeg-bytes')
        self.assertEqual(r.status_code, 201, r.get_json())
        job = r.get_json()
        self.assertTrue(job['test_mode'])          # audit not passed yet
        state = self.wait(job['id'])
        self.assertEqual(state['status'], 'done', state)
        self.assertEqual(state['url'], 'https://youtu.be/fixture0001')
        self.assertEqual(state['percent'], 100)
        self.assertFalse(self.asset_video(asset).exists())
        self.assertEqual(self.control['inserts'], 1)
        self.assertEqual(self.control['thumbnails'], 1)
        body = self.control['last_insert']['body']
        self.assertEqual(body['status'], {'privacyStatus': 'private', 'selfDeclaredMadeForKids': False})
        self.assertEqual(body['snippet']['title'], '제목 〈1〉')
        self.assertTrue(any('〈 〉' in w for w in state['warnings']))
        self.assertTrue(any('썸네일 설정을 요청' in w for w in state['warnings']))
        # The finished job is what the sheet submit may reference.
        with appmod.app.app_context():
            s = svc.Service(appmod.app.extensions['hooks_service'](), self.cfg)
        self.assertEqual(s.job_for_submit(job['id'], 'item-1', 'conn')['video_id'], 'fixture0001')
        self.assertIsNone(s.job_for_submit(job['id'], 'item-2', 'conn'))
        self.assertIsNone(s.job_for_submit(job['id'], 'item-1', 'other'))
        self.assertIsNone(s.job_for_submit('0' * 32, 'item-1', 'conn'))
        # Lookup by item adopts the finished job; a second job for the same item needs force.
        self.assertEqual(self.client.get('/api/youtube/jobs?item_id=item-1&connection=conn').get_json()['job']['id'], job['id'])
        r = self.create(item_id='item-1')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()['code'], 'job_exists')
        self.assertEqual(r.get_json()['job']['id'], job['id'])
        forced = self.create(item_id='item-1', force=True)
        self.assertEqual(forced.status_code, 201)
        self.wait(forced.get_json()['id'])
        self.assertEqual(len(self.client.get('/api/youtube/jobs').get_json()['jobs']), 2)
        # Hooks history hides YouTube jobs.
        self.assertEqual(self.client.get('/api/hooks/jobs').get_json()['jobs'], [])

    def test_audit_mode_checks_apps_script_version(self):
        self.connect()
        self.settings['youtube_audit_passed'] = True
        with patch.object(self.cfg, 'script_version', lambda: 14):
            r = self.create()
            self.assertEqual(r.status_code, 409)
            self.assertEqual(r.get_json()['code'], 'upgrade_required')
        with patch.object(self.cfg, 'script_version', lambda: None):
            self.assertEqual(self.create().get_json()['code'], 'upgrade_required')
        r = self.create()
        self.assertEqual(r.status_code, 201)
        self.assertFalse(r.get_json()['test_mode'])
        self.wait(r.get_json()['id'])

    def test_thumbnail_failure_is_a_warning_and_bad_thumbnail_rejected(self):
        self.connect()
        self.assertEqual(self.create(thumbnail=b'not-an-image').get_json()['code'], 'bad_thumbnail')
        self.control['thumb_fail'] = True
        state = self.wait(self.create(thumbnail=b'\xff\xd8\xffjpeg').get_json()['id'])
        self.assertEqual(state['status'], 'done')
        self.assertTrue(any('썸네일은 설정하지 못했습니다' in w and '권한' in w for w in state['warnings']))
        self.control.clear()
        state = self.wait(self.create(item_id='item-2').get_json()['id'])
        self.assertEqual(self.control['thumbnails'], 0)

    def test_api_failure_keeps_source_and_retry_succeeds(self):
        self.connect()
        asset = self.upload_asset()
        self.control['fail'] = (403, 'quotaExceeded')
        ident = self.create(asset=asset).get_json()['id']
        state = self.wait(ident)
        self.assertEqual(state['status'], 'error')
        self.assertEqual(state['error_code'], 'quotaExceeded')
        self.assertIn('한도', state['message'])
        self.assertNotIn('HttpError', state['message'])
        self.assertTrue(self.asset_video(asset).exists())
        self.control.pop('fail')
        r = self.client.post(f'/api/youtube/jobs/{ident}/retry', json={}, headers=H)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.wait(ident)['status'], 'done')
        self.assertEqual(self.client.post(f'/api/youtube/jobs/{ident}/retry', json={}, headers=H).get_json()['code'], 'not_retryable')

    def test_invalid_grant_disconnects(self):
        self.connect()
        self.control['fail'] = (401, 'authError')
        state = self.wait(self.create().get_json()['id'])
        self.assertEqual(state['error_code'], 'invalid_grant')
        self.assertFalse(self.client.get('/api/youtube/status').get_json()['connected'])

    def test_interrupted_job_after_restart_warns_about_duplicates(self):
        self.connect()
        asset = self.upload_asset()
        ident = self.create(asset=asset).get_json()['id']
        self.wait(ident)
        with appmod.app.app_context():
            store = appmod.app.extensions['hooks_service']().store
        store.update(ident, busy=True, percent=99, status='finalizing')
        self.asset_video(asset).write_bytes(self.bytes)          # source still present, as after a crash mid-upload
        from hooks.store import Store
        Store(self.tmp.name)                                       # boot-time recovery
        self.assertEqual(self.client.get(f'/api/youtube/jobs/{ident}').get_json()['status'], 'interrupted')
        r = self.client.post(f'/api/youtube/jobs/{ident}/retry', json={}, headers=H)
        self.assertEqual(r.status_code, 200)
        state = self.wait(ident)
        self.assertTrue(any('Studio' in w for w in state['warnings']))

    def test_rejects_bad_payloads(self):
        self.connect()
        for payload in ['x', {'asset_id': 'a'}, {'asset_id': 1, 'item_id': 'i', 'connection': 'c', 'title': 't', 'description': 'd'},
                        {'asset_id': '0' * 32, 'item_id': 'i', 'connection': 'c', 'title': 't', 'description': 'd', 'extra': 1}]:
            r = self.client.post('/api/youtube/jobs', data={'payload': json.dumps(payload)}, headers=H)
            self.assertEqual(r.status_code, 400, payload)
        r = self.client.post('/api/youtube/jobs', data={'payload': json.dumps({'asset_id': '0' * 32, 'item_id': 'i', 'connection': 'c', 'title': 't', 'description': 'd'})}, headers=H)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.create(title='가' * 101).get_json()['code'], 'bad_title')
        self.assertEqual(self.client.get('/api/youtube/jobs/not-an-id').status_code, 400)
        self.assertEqual(self.client.get('/api/youtube/jobs/' + '0' * 32).status_code, 404)

    # ---- access control ----------------------------------------------------------
    def test_local_mode_requires_same_origin_for_youtube_writes(self):
        self.connect()
        self.assertEqual(self.client.post('/api/youtube/settings', json={'audit_passed': True}).status_code, 403)
        self.assertEqual(self.client.post('/api/youtube/settings', json={'audit_passed': True}, headers={'Origin': 'https://evil.test'}).status_code, 403)
        self.assertEqual(self.client.get('/api/youtube/status').status_code, 200)
        self.assertEqual(self.client.get('/api/youtube/status', headers={'Host': 'evil.test'}).status_code, 400)
        self.assertEqual(self.client.get('/api/youtube/status', headers={'Host': '127.0.0.1:8765'}).status_code, 200)

    def test_password_mode_requires_login_for_callback_and_jobs(self):
        from flask import Flask
        from access_control import install_access_control
        from hooks.routes import install as install_hooks
        from youtube_upload.routes import install as install_youtube
        secured = Flask('secured', template_folder=str(Path(appmod.__file__).parent / 'templates'))
        with patch.dict(os.environ, {'APP_PASSWORD': 'fixture-password', 'APP_PUBLIC_ORIGIN': ''}):
            install_access_control(secured)
        secured.config['HOOKS_DATA_DIR'] = str(Path(self.tmp.name) / 'secured')
        secured.config['YOUTUBE_CREDENTIALS_FILE'] = str(Path(self.tmp.name) / 'secured-creds.json')
        install_hooks(secured, lambda *a: None, lambda: {'backend': 'codex', 'model': 'fixture'})
        install_youtube(secured, lambda: {}, lambda d: None, __import__('threading').Lock(), appmod.sniff_image, lambda: 15, 8765, '', appmod.THUMB_MAX_BYTES)
        client = secured.test_client()
        self.assertEqual(client.get('/api/youtube/oauth/callback?state=x&code=y').status_code, 401)
        self.assertEqual(client.get('/api/youtube/status').status_code, 401)
        client.post('/login', data={'password': 'fixture-password'}, headers=H)
        self.assertEqual(client.get('/api/youtube/status').status_code, 200)
        self.assertEqual(client.post('/api/youtube/jobs', data={'payload': '{}'}).status_code, 403)
        self.assertEqual(client.post('/api/youtube/jobs', data={'payload': '{}'}, headers=H).status_code, 400)


if __name__ == '__main__':
    unittest.main()
