"""Upload funnel HTTP contract, validation and delayed-CSV acknowledgement (offline)."""
import io
import json
import unittest
from unittest.mock import patch
import app as m

SHEET = {'sheet_id': 'sheet', 'gid': '0', 'url': 'https://docs.google.com/spreadsheets/d/sheet/edit'}
WRITER = {'url': 'https://script.google.com/macros/s/test/exec', 'token': 'private'}
ID = '12345678-1234-1234-1234-123456789abc'


class UploadProcessTest(unittest.TestCase):
    def setUp(self):
        self.client = m.app.test_client()
        self.cfg = patch.multiple(m, get_sheet_setting=lambda: SHEET, get_upload_setting=lambda: WRITER)
        self.cfg.start()
        self.addCleanup(self.cfg.stop)
        m._recent_edits.clear(); m._recent_thumbs.clear()
        self.cells = {'itemId': 'item', 'status': '✅ 업로드 완료', 'title': 'title', 'desc': 'description',
                      'srcUrl': 'https://youtu.be/abcdefghijk', 'refUrls': 'https://youtu.be/lmnopqrstuv',
                      'memo': '', 'thumbUrl': 'https://example.com/thumb.jpg'}
        self.result = {'ok': True, 'row': 9, 'cells': self.cells, 'revision': 'a'*64, 'submitted': True, 'requestId': ID}

    def payload(self):
        return {'connection': m.upload_connection(), 'item_id': 'item', 'request_id': ID, 'revision': 'a'*64,
                'fields': {'title': 'title', 'src_url': 'https://youtu.be/abcdefghijk', 'ref_urls': 'https://youtu.be/lmnopqrstuv',
                           'memo': '', 'desc': 'description'}, 'thumbnail_mode': 'existing'}

    def post(self, payload=None, image=None):
        data = {'payload': json.dumps(self.payload() if payload is None else payload)}
        if image is not None: data['file'] = (io.BytesIO(image), 'image.jpg')
        response = self.client.post('/api/upload-process/submit', data=data)
        response.request.close()
        return response

    def test_page_and_shared_helper(self):
        for path in ['/upload-process', '/helper', '/', '/shorts', '/references']:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200)
            self.assertIn('업로드 프로세스'.encode(), r.data)
        html = self.client.get('/upload-process').get_data(as_text=True)
        self.assertIn('recipe-editor.js', html)
        self.assertIn('thumbnail.js', html)
        self.assertNotIn('id="videoDescription"', html)

    def test_get_reads_live_and_checks_connection(self):
        with patch.object(m, 'apps_script_post', return_value=self.result) as post:
            r = self.client.get('/api/upload-process/items/item')
            self.assertEqual(r.status_code, 200)
            self.assertEqual(post.call_args.args[1]['action'], 'upload_get')
            self.assertNotIn('private', r.get_data(as_text=True))
            self.assertEqual(r.json['item']['row'], 9)
        with patch.object(m, 'apps_script_post') as post:
            self.assertEqual(self.client.get('/api/upload-process/items/item?connection=wrong').status_code, 409)
            post.assert_not_called()

    def test_submit_status_is_owned_by_server_and_acks_thumbnail(self):
        with patch.object(m, 'apps_script_post', return_value=self.result) as post:
            r = self.post()
            self.assertEqual(r.status_code, 200)
            body = post.call_args.args[1]
            self.assertEqual(body['action'], 'upload_submit')
            self.assertNotIn('status', body['fields'])
            self.assertEqual(body['requestId'], ID)
            self.assertEqual(r.json['item']['status'], 'uploaded')
            self.assertEqual(m._recent_edits[-1]['kind'], 'thumbnail')
            self.assertTrue(any(x['kind'] == 'update' for x in m._recent_edits))

    def test_rejects_invalid_fields_before_network(self):
        bad = [None, [], {}, 'text']
        for fields in bad + [dict(self.payload()['fields'], title=''), dict(self.payload()['fields'], title='x'*501),
                             dict(self.payload()['fields'], status='uploaded'), dict(self.payload()['fields'], desc='=')]:
            payload = self.payload(); payload['fields'] = fields
            with patch.object(m, 'apps_script_post') as post:
                self.assertEqual(self.post(payload).status_code, 400)
                post.assert_not_called()
        for key, value in [('revision','bad'),('request_id','bad'),('connection','old'),('item_id',{}),('thumbnail_mode','skip')]:
            payload = self.payload(); payload[key] = value
            with patch.object(m, 'apps_script_post') as post:
                self.assertIn(self.post(payload).status_code, [400,409]); post.assert_not_called()

    def test_image_validation(self):
        payload = self.payload(); payload['thumbnail_mode'] = 'new'
        with patch.object(m, 'apps_script_post', return_value=self.result) as post:
            self.assertEqual(self.post(payload).status_code, 400)
            self.assertEqual(self.post(payload,b'bad').status_code, 400)
            self.assertEqual(self.post(payload,b'x'*(m.THUMB_MAX_BYTES+1)).status_code, 413)
            self.assertEqual(self.post(image=b'\xff\xd8\xffjpeg').status_code, 400)
            post.assert_not_called()
            self.assertEqual(self.post(payload,b'\xff\xd8\xffjpeg').status_code, 200)
            self.assertEqual(post.call_args.args[1]['mime'], 'image/jpeg')

    def test_errors_and_receipt_lookup(self):
        for code in ['conflict','already_uploaded','commit_unknown','commit_failed','sheets_service_required','request_mismatch']:
            with patch.object(m, 'apps_script_post', return_value={'ok':False,'error':code}):
                r = self.post()
                self.assertEqual(r.json['code'], code)
                self.assertGreaterEqual(r.status_code, 400)
        with patch.object(m, 'apps_script_post', return_value=self.result) as post:
            r = self.client.get('/api/upload-process/items/item?request_id='+ID)
            self.assertTrue(r.json['submitted'])
            self.assertEqual(post.call_args.args[1]['requestId'], ID)
        with patch.object(m, 'apps_script_post', side_effect=RuntimeError('timeout')):
            self.assertEqual(self.post().json['code'], 'commit_unknown')

    def test_wrong_item_response_is_not_acknowledged(self):
        wrong = {**self.result, 'cells': {**self.cells, 'itemId': 'other'}}
        with patch.object(m, 'apps_script_post', return_value=wrong):
            self.assertEqual(self.post().json['code'], 'commit_unknown')
            self.assertEqual(m._recent_edits, [])

    def test_youtube_link_is_derived_from_a_finished_job(self):
        job = {'video_id': 'abcdefghijk', 'status': 'done'}
        payload = self.payload(); payload['youtube_job_id'] = '0' * 32
        with patch.object(m, 'youtube_job_for_submit', return_value=job) as lookup, \
             patch.object(m, 'apps_script_post', return_value={**self.result, 'cells': {**self.cells, 'youtubeUrl': 'https://youtu.be/abcdefghijk'}}) as post:
            r = self.post(payload)
            self.assertEqual(r.status_code, 200, r.json)
            self.assertEqual(lookup.call_args.args, ('0' * 32, 'item', m.upload_connection()))
            body = post.call_args.args[1]
            self.assertEqual(body['youtubeUrl'], 'https://youtu.be/abcdefghijk')
            self.assertNotIn('youtubeUrl', body['fields'])
            self.assertEqual(r.json['warnings'], [])
        # Apps Script older than v15 ignores the key: the link is reported back instead of silently lost.
        with patch.object(m, 'youtube_job_for_submit', return_value=job), patch.object(m, 'apps_script_post', return_value=self.result):
            r = self.post(payload)
            self.assertEqual(r.status_code, 200)
            self.assertTrue(any('https://youtu.be/abcdefghijk' in w and '15' in w for w in r.json['warnings']))
        # No finished job for this item: refuse before touching the sheet.
        with patch.object(m, 'youtube_job_for_submit', return_value=None), patch.object(m, 'apps_script_post') as post:
            r = self.post(payload)
            self.assertEqual((r.status_code, r.json['code']), (409, 'youtube_job_invalid'))
            post.assert_not_called()
        # A browser-supplied URL is not accepted at all.
        payload = self.payload(); payload['youtube_url'] = 'https://youtu.be/abcdefghijk'
        with patch.object(m, 'apps_script_post') as post:
            self.assertEqual(self.post(payload).status_code, 400)
            post.assert_not_called()

    def test_connection_detects_writer_or_tab_change(self):
        original = m.upload_connection()
        with patch.object(m,'get_upload_setting',return_value={**WRITER,'token':'rotated'}):
            self.assertNotEqual(original,m.upload_connection())
        with patch.object(m,'get_sheet_setting',return_value={**SHEET,'gid':'12'}):
            self.assertNotEqual(original,m.upload_connection())


if __name__ == '__main__': unittest.main()
