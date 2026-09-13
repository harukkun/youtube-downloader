"""Board item API, stable identity and delayed CSV reconciliation (no network)."""
import csv
import io
import unittest
from unittest.mock import patch

import app as m
from tests.test_shorts import SHEET, UPLOADER, JPEG


def item(ident='a', row=3, **cells):
    return m.item_from_script(row, {'itemId': ident, 'status': '⭐ 촬영 후보', 'dish': ident,
                                    'createdAt': '2026-09-12 10:00', 'updatedAt': '2026-09-12 10:00', **cells})


class BoardParseTest(unittest.TestCase):
    def test_metadata_only_rows_are_not_items(self):
        text = '📌 상태,🕒 수정일,🆔 항목 ID\n,2026-09-11 20:48,orphan\n'
        self.assertEqual(m.parse_sheet_items(text), [])
        self.assertIsNone(m.item_from_script(23, {'itemId': 'orphan',
                                                'updatedAt': '2026-09-11 20:48', 'youtubeOn': False}))

    def test_untitled_rows_with_real_content_are_preserved(self):
        for cells in [{'memo': '메모'}, {'refUrls': 'https://example.com'},
                      {'desc': '설명'}, {'status': '촬영 후보'}, {'youtubeOn': True},
                      {'youtubeUrl': 'https://example.com'}, {'createdAt': '2026-09-12 10:00'}]:
            with self.subTest(cells=cells):
                self.assertIsNotNone(m.item_from_script(3, {'itemId': 'keep', **cells}))

    def test_blank_registered_row_and_legacy(self):
        out = m.parse_sheet_items('📌 상태,📅 등록일\n,2026-09-12 10:00\n,\n')
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['item_id'], '')
        self.assertEqual(out[0]['created_at'], '2026-09-12T10:00')

    def test_script_csv_match_and_channel_only(self):
        cells = {'itemId': 'a', 'status': '✅ 업로드 완료', 'dish': '요리', 'refChannels': '\n채널\n추가\n',
                 'refUrls': '', 'youtubeOn': True, 'naverClipOn': False, 'createdAt': '2026-09-12 10:00'}
        keys = list(m.SCRIPT_FIELDS)
        headers = {v: k for k, v in m.SHEET_HEADER_KEYS.items()}
        buf = io.StringIO(); writer = csv.writer(buf)
        writer.writerow([headers[k] for k in keys])
        writer.writerow([cells.get(m.SCRIPT_FIELDS[k], '') for k in keys])
        parsed = m.parse_sheet_items(buf.getvalue())[0]
        self.assertEqual(parsed, m.item_from_script(2, cells))
        self.assertEqual(parsed['ref_channels'], '\n채널\n추가\n')
        self.assertTrue(parsed['platforms']['youtube']['checked'])


class BoardItemApiTest(unittest.TestCase):
    def setUp(self):
        self.client = m.app.test_client()
        m._recent_edits.clear(); m._recent_thumbs.clear()
        self.cfg = patch.multiple(m, get_sheet_setting=lambda: SHEET, get_upload_setting=lambda: UPLOADER)
        self.cfg.start(); self.addCleanup(self.cfg.stop)
        self.response = {'ok': True, 'row': 5, 'cells': {'itemId':'a','dish':'new','status':'⭐ 촬영 후보'}}

    def test_update(self):
        m._sheet_cache['at'] = 123
        with patch.object(m, 'apps_script_post', return_value=self.response) as post:
            r = self.client.put('/api/shorts/items/3', json={'item_id':'a','fields':{'dish':'new','naver_clip_on':False,'status':'candidate'}})
        self.assertEqual(r.status_code, 200)
        payload = post.call_args.args[1]
        self.assertEqual(payload['action'], 'board_update')
        self.assertEqual(payload['itemId'], 'a')
        self.assertEqual(payload['fields'], {'dish':'new','naverClipOn':False,'status':m.STATUSES['candidate']})
        self.assertEqual(r.json['item']['row'], 5)
        self.assertEqual(r.json['item']['id'], 'a')
        self.assertEqual(m._sheet_cache['at'], 0)
        self.assertEqual(len(m._recent_edits), 1)

    def test_validation(self):
        cases = [{}, {'what':'x'}, {'status':'no'}, {'dish':'a'*201}, {'desc':'x'*20001},
                 {'pinned':'x'*20001}, {'youtube_on':'false'}, {'youtube_on':1}, {'item_id':'x'},
                 {'created':'2020'}, {'thumb_url':'https://x'}, {'ref_channels':[1]}, {'memo':None}]
        with patch.object(m, 'apps_script_post') as post:
            for fields in cases:
                r = self.client.put('/api/shorts/items/3', json={'item_id':'a','fields':fields})
                self.assertEqual(r.status_code, 400, fields.keys())
            self.assertEqual(self.client.put('/api/shorts/items/2', json={}).status_code, 400)
            self.assertEqual(self.client.put('/api/shorts/items/3', json={'fields':{'dish':'x'}}).status_code, 409)
            self.assertEqual(self.client.post('/api/shorts/items', json=[]).status_code, 400)
            post.assert_not_called()

    def test_text_and_formula(self):
        with patch.object(m, 'apps_script_post', return_value=self.response) as post:
            r = self.client.put('/api/shorts/items/3', json={'item_id':'a','fields':{'dish':'  ==SUM(A1) ', 'ref_channels':['','채널','']}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(post.call_args.args[1]['fields'], {'dish':'SUM(A1)','refChannels':'\n채널\n'})

    def test_errors(self):
        for code, status in {'row_mismatch':409,'locked_platform':409,'bad_status':400,'bad_fields':400,'bad_row':400,'busy':503,'unknown_action':409,'weird':502}.items():
            with patch.object(m, 'apps_script_post', return_value={'ok':False,'error':code}):
                r = self.client.put('/api/shorts/items/3', json={'item_id':'a','fields':{'dish':'x'}})
            self.assertEqual(r.status_code, status, code)
            self.assertEqual(r.json['code'], code)

    def test_add_delete_preserve_pending_and_warnings(self):
        m.remember_recent_edit('update', item('older'), SHEET)
        with patch.object(m, 'apps_script_post', return_value={**self.response,'warnings':['레퍼 동기화 실패']}) as post:
            added = self.client.post('/api/shorts/items', json={})
            self.assertEqual(post.call_args.args[1]['fields'], {})
            deleted = self.client.delete('/api/shorts/items/3', json={'item_id':'a'})
        self.assertEqual(added.status_code, 200)
        self.assertEqual(deleted.json['row'], 5)
        self.assertEqual(deleted.json['warnings'], ['레퍼 동기화 실패'])
        self.assertEqual([op['kind'] for op in m._recent_edits], ['update','insert','delete'])

    def test_requires_connection_and_valid_response(self):
        with patch.object(m, 'get_sheet_setting', return_value=None):
            self.assertEqual(self.client.post('/api/shorts/items', json={}).status_code, 400)
        with patch.object(m, 'get_upload_setting', return_value=None):
            self.assertEqual(self.client.post('/api/shorts/items', json={}).status_code, 400)
        with patch.object(m, 'apps_script_post', return_value={'ok':True}):
            self.assertEqual(self.client.post('/api/shorts/items', json={}).status_code, 502)

    def test_thumbnail_id(self):
        with patch.object(m, 'apps_script_post', return_value={'ok':True,'row':8,'url':'https://x/new'}) as post:
            r = self.client.post('/api/shorts/thumbnail', data={'row':'3','item_id':'a','file':(io.BytesIO(JPEG),'a.jpg')})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(post.call_args.args[1]['itemId'], 'a')
        self.assertEqual(m._recent_edits[0]['kind'], 'thumbnail')
        self.assertEqual(m._recent_edits[0]['row'], 8)


class RecentEditTest(unittest.TestCase):
    def setUp(self):
        m._recent_edits.clear(); m._recent_thumbs.clear()

    def apply(self, items):
        return m.apply_recent_edits(items, SHEET)

    def remember(self, kind, it):
        m.remember_recent_edit(kind, it, SHEET)

    def test_changed_identity_fields_and_shift(self):
        old = item('a', 8)
        new = item('a', 3, dish='new', srcUrl='https://youtu.be/12345678901', title='title')
        self.remember('update', new)
        out = self.apply([old, item('b', 9)])
        self.assertEqual(out[0]['dish_title'], 'new')
        self.assertEqual(out[0]['row'], 8)
        self.assertEqual(old['dish_title'], 'a')
        self.apply([{**new,'row':8}])
        self.assertEqual(m._recent_edits, [])

    def test_preserves_independent_remote_changes(self):
        old = item('a')
        changed = item('a', memo='local')
        m.remember_recent_edit('update', changed, SHEET, before=old)
        remote = item('a', dish='remote')
        out = self.apply([remote])[0]
        self.assertEqual(out['memo'], 'local')
        self.assertEqual(out['dish_title'], 'remote')
        self.apply([item('a', dish='remote', memo='local', updatedAt='2026-09-12 11:00')])
        self.assertEqual(m._recent_edits, [])

    def test_different_id_not_overlaid(self):
        self.remember('update', item('a', dish='new'))
        self.assertEqual(self.apply([item('b')])[0]['dish_title'], 'b')

    def test_multiple_insert_partial_convergence(self):
        base = item('base')
        a, b = item('a'), item('b')
        self.remember('insert', a); self.remember('insert', b)
        expected = [('b',3),('a',4),('base',5)]
        self.assertEqual([(x['id'],x['row']) for x in self.apply([base])], expected)
        self.assertEqual([(x['id'],x['row']) for x in self.apply([a,{**base,'row':4}])], expected)
        self.apply([b,{**a,'row':4},{**base,'row':5}])
        self.assertEqual(m._recent_edits, [])

    def test_insert_update_then_delete(self):
        base = item('base'); a = item('a'); edited = item('a',dish='edited')
        self.remember('insert',a); self.remember('update',edited)
        self.assertEqual(self.apply([base])[0]['dish_title'],'edited')
        self.assertEqual(self.apply([a,{**base,'row':4}])[0]['dish_title'],'edited')
        self.remember('delete',edited)
        self.assertEqual([(x['id'],x['row']) for x in self.apply([a,{**base,'row':4}])],[('base',3)])
        self.apply([base]); self.assertEqual(m._recent_edits,[])

    def test_insert_deleted_before_csv_ever_saw_it(self):
        self.remember('insert', item('a'))
        self.remember('delete', item('a'))
        self.assertEqual([x['id'] for x in self.apply([item('base')])], ['base'])
        # A delayed intermediate export must not resurrect the deleted item.
        self.assertEqual([x['id'] for x in self.apply([item('a'), item('base', 4)])], ['base'])
        self.apply([item('base')])
        self.assertEqual(m._recent_edits, [])

    def test_skipped_intermediate_updates(self):
        self.remember('update',item('a',dish='first'))
        last=item('a',dish='last');self.remember('update',last)
        self.apply([last]);self.assertEqual(m._recent_edits,[])

    def test_thumbnail_and_structure(self):
        old=item('a'); new=item('a',dish='new')
        m.remember_recent_thumb(3,'https://x/new','', 'a',SHEET)
        self.remember('update',new);self.remember('insert',item('b'))
        out=self.apply([old]);self.assertEqual(out[1]['video']['thumbnail'],'https://x/new')
        self.assertEqual(out[1]['dish_title'],'new');self.assertEqual(out[1]['row'],4)
        self.assertEqual(old['video']['thumbnail'],'')
        self.apply([item('b'),item('a',4,dish='new',thumbUrl='https://x/new')])
        self.assertEqual(m._recent_edits,[])

    def test_expiry_and_other_sheet(self):
        self.remember('update',item('a',dish='new'))
        other={**SHEET,'sheet_id':'other'}
        self.assertEqual(m.apply_recent_edits([item('a')],other)[0]['dish_title'],'a')
        m._recent_edits[0]['at']-=m.RECENT_EDIT_TTL+1
        self.assertEqual(self.apply([item('a')])[0]['dish_title'],'a')
        self.assertEqual(m._recent_edits,[])

    def test_disconnect_clears_pending(self):
        self.remember('insert',item('a'))
        with patch.object(m,'load_settings',return_value={}),patch.object(m,'save_settings'):
            self.assertEqual(m.app.test_client().post('/api/shorts/sheet',json={'url':''}).status_code,200)
        self.assertEqual(m._recent_edits,[])
