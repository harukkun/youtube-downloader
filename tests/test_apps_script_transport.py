import subprocess
import unittest
from unittest.mock import patch
import app as m

class AppsScriptTransportTest(unittest.TestCase):
    def test_read_transport_keeps_credentials_off_command_line(self):
        result = subprocess.CompletedProcess([], 0, b'{"ok":true,"items":[],"nextCursor":null}\n200', b'')
        with patch.object(m.shutil, 'which', return_value='/usr/bin/curl'), patch.object(m.subprocess, 'run', return_value=result) as run:
            response = m.apps_script_post('https://script.google.com/macros/s/test/exec', {'action':'cook_backfill_list','token':'secret-token'})
            self.assertTrue(response['ok'])
            self.assertNotIn('secret-token', str(run.call_args.args))
            self.assertIn(b'secret-token', run.call_args.kwargs['input'])
            self.assertEqual(run.call_args.kwargs['timeout'], 30)

    def test_transport_errors_are_retryable(self):
        for result in [subprocess.CompletedProcess([], 0, b'not found\n404', b''),
                       subprocess.CompletedProcess([], 28, b'', b'timeout')]:
            with patch.object(m.subprocess, 'run', return_value=result):
                with self.assertRaises(m.AppsScriptTransportError):
                    m.apps_script_read_curl('https://example.com', b'{}', 'curl')

    def test_writes_keep_existing_transport(self):
        with patch.object(m, 'apps_script_read_curl') as read, patch.object(m.urllib.request, 'urlopen', side_effect=OSError('offline')):
            with self.assertRaises(RuntimeError):
                m.apps_script_post('https://example.com', {'action':'cook_backfill_save'})
            read.assert_not_called()

    def test_upload_transport_does_not_replay_uncertain_writes(self):
        result = subprocess.CompletedProcess([], 0, b'not found\n404', b'')
        with patch.object(m.shutil, 'which', return_value='/usr/bin/curl'), patch.object(m.subprocess, 'run', return_value=result) as run:
            with self.assertRaises(m.AppsScriptTransportError):
                m.apps_script_post('https://script.google.com/macros/s/test/exec', {'action':'upload_submit'})
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.kwargs['timeout'], m.SHORTS_UPLOAD_TIMEOUT + 5)
