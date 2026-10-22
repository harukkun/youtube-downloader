import unittest
from unittest.mock import patch
import app as m


class CookBackfillTest(unittest.TestCase):
    def setUp(self):
        self.client = m.app.test_client()
        self.addCleanup(patch.stopall)
        patch.object(m, 'upload_connection', return_value='connection').start()
        patch.object(m, 'get_recipe_settings', return_value={'backend': 'codex', 'model': 'test'}).start()
        self.source = {'ok': True, 'item': {'description': '마늘 1/2큰술을 볶는다.', 'title': '마늘밥', 'sourceChannel': '원본 채널'}}
        self.recipe = {'title': '마늘밥', 'author': '', 'baseServings': None, 'difficulty': None, 'category': None,
                       'ingredients': [{'name': '마늘', 'amount': '1/2', 'unit': '큰술', 'note': None, 'isMain': True}],
                       'steps': ['마늘을 볶는다.']}
        self.body = {'connection': 'connection', 'revision': 'a' * 64}

    def test_preview_does_not_extract_or_write(self):
        with patch.object(m, 'sheet_call', return_value={'ok': True, 'items': [], 'nextCursor': None}) as call, patch.object(m, 'llm_structured') as llm:
            result = self.client.get('/api/cook-dict/backfill?cursor=53')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json['connection'], 'connection')
            call.assert_called_once_with('cook_backfill_list', cursor=53)
            llm.assert_not_called()

    def test_extracts_from_current_sheet_without_photo(self):
        with patch.object(m, 'sheet_call', side_effect=[self.source, {'ok': True, 'saved': True}]) as call, patch.object(m, 'llm_structured', return_value=self.recipe) as llm:
            result = self.client.post('/api/cook-dict/backfill/item', json=self.body)
            self.assertEqual(result.status_code, 200)
            self.assertTrue(result.json['saved'])
            self.assertIn(self.source['item']['description'], llm.call_args.args[1])
            saved = call.call_args.kwargs
            self.assertEqual(saved['cookDict']['description'], self.source['item']['description'])
            self.assertEqual(saved['cookDict']['author'], '')
            self.assertNotIn('cookPhoto', saved)
            self.assertEqual(saved['revision'], self.body['revision'])

    def test_retry_skips_existing_without_ai(self):
        with patch.object(m, 'sheet_call', return_value={'ok': True, 'skipped': True}) as call, patch.object(m, 'llm_structured') as llm:
            self.assertTrue(self.client.post('/api/cook-dict/backfill/item', json=self.body).json['skipped'])
            self.assertEqual(call.call_count, 1)
            llm.assert_not_called()

    def test_failures_never_save(self):
        for raw in [{'ingredients': []}, RuntimeError('AI unavailable')]:
            with patch.object(m, 'sheet_call', return_value=self.source) as call, patch.object(m, 'llm_structured', **({'side_effect': raw} if isinstance(raw, Exception) else {'return_value': raw})):
                self.assertEqual(self.client.post('/api/cook-dict/backfill/item', json=self.body).status_code, 502)
                self.assertEqual(call.call_count, 1)
        with patch.object(m, 'sheet_call') as call:
            self.assertEqual(self.client.post('/api/cook-dict/backfill/item', json={**self.body, 'connection': 'other'}).status_code, 409)
            call.assert_not_called()

    def test_blank_description_and_changed_source(self):
        self.source['item']['description'] = ''
        with patch.object(m, 'sheet_call', return_value=self.source), patch.object(m, 'llm_structured') as llm:
            self.assertEqual(self.client.post('/api/cook-dict/backfill/item', json=self.body).status_code, 400)
            llm.assert_not_called()
        with patch.object(m, 'sheet_call', side_effect=m.SheetCallError('changed', 409, 'conflict')), patch.object(m, 'llm_structured') as llm:
            self.assertEqual(self.client.post('/api/cook-dict/backfill/item', json=self.body).status_code, 409)
            llm.assert_not_called()

    def test_transient_404_and_redirect_to_service_response_are_retried(self):
        page = {'ok': True, 'items': [], 'nextCursor': None}
        with patch.object(m, 'sheet_call', side_effect=[m.SheetCallError('404', 502, 'transport_retryable'),
                {'ok': True, 'service': 'shorts-thumbnail', 'version': 18}, page]) as call, patch.object(m.time, 'sleep'):
            response = self.client.get('/api/cook-dict/backfill')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['items'], [])
            self.assertEqual(call.call_count, 3)

    def test_retry_limit_and_authorization_failures(self):
        with patch.object(m, 'sheet_call', return_value={'ok': True, 'service': 'shorts-thumbnail'}) as call, patch.object(m.time, 'sleep'):
            response = self.client.get('/api/cook-dict/backfill')
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json['code'], 'read_failed')
            self.assertEqual(call.call_count, 3)
        with patch.object(m, 'sheet_call', side_effect=m.SheetCallError('unauthorized', 401, 'unauthorized')) as call:
            self.assertEqual(self.client.get('/api/cook-dict/backfill').status_code, 401)
            self.assertEqual(call.call_count, 1)
        with self.assertRaises(ValueError):
            m.cook_backfill_read('cook_backfill_save')

    def test_writes_are_not_automatically_replayed(self):
        with patch.object(m, 'sheet_call', side_effect=[self.source, m.SheetCallError('404', 502, 'transport_retryable')]) as call, patch.object(m, 'llm_structured', return_value=self.recipe):
            self.assertEqual(self.client.post('/api/cook-dict/backfill/item', json=self.body).status_code, 502)
            self.assertEqual(call.call_count, 2)
