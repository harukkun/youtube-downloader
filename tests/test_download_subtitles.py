"""Subtitle selection and offline yt-dlp subtitle-only download regression tests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as appmod


class SubtitleDownloadTest(unittest.TestCase):
    def test_options_only_include_convertible_subtitles(self):
        self.assertEqual(appmod.build_subtitle_options({}), [])
        options = appmod.build_subtitle_options({
            'subtitles': {'ko': [{'ext': 'vtt', 'name': '한국어'}], 'live_chat': [{'ext': 'json'}]},
            'automatic_captions': {'en': [{'ext': 'srt', 'name': 'English'}]},
        })
        self.assertEqual([o['id'] for o in options], ['subtitle:manual:ko', 'subtitle:auto:en'])
        self.assertIn('자동 생성', options[1]['label'])

    def test_subtitle_only_download_and_conversion(self):
        for source, ext in [('manual', 'srt'), ('auto', 'vtt')]:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                original = root / f'original.{ext}'
                original.write_text('1\n00:00:00,000 --> 00:00:01,000\n안녕하세요\n' if ext == 'srt'
                                    else 'WEBVTT\n\n00:00.000 --> 00:01.000\n안녕하세요\n')
                target = root / 'downloads'
                quality = f'subtitle:{source}:ko'
                info = {
                    'id': 'test', 'title': 'Subtitle test', 'extractor': 'test',
                    'webpage_url': 'https://example.com/watch',
                    'url': (root / 'absent-video.mp4').as_uri(), 'ext': 'mp4',
                    'subtitles' if source == 'manual' else 'automatic_captions': {
                        'ko': [{'ext': ext, 'url': original.as_uri()}]},
                }
                build_opts = appmod.build_ydl_opts
                def offline_opts(*args):
                    return {**build_opts(*args), 'enable_file_urls': True}
                job_id = f'test-subtitle-{source}'
                appmod.jobs[job_id] = {'quality': quality}
                try:
                    with patch.object(appmod, 'get_download_dir', return_value=target), \
                         patch.object(appmod, 'build_ydl_opts', side_effect=offline_opts), \
                         patch.object(appmod.yt_dlp.YoutubeDL, 'extract_info', return_value=info), \
                         patch.object(appmod, 'history_update') as history:
                        appmod.run_download(job_id, 'https://example.com/watch', quality)
                    job = appmod.jobs[job_id]
                    self.assertEqual(job['status'], 'finished', job)
                    saved = Path(job['filepath'])
                    self.assertEqual(saved.suffix, '.srt')
                    self.assertIn('안녕하세요', saved.read_text())
                    self.assertEqual(list(target.iterdir()), [saved])
                    self.assertEqual(history.call_args.kwargs['filepath'], str(saved))
                finally:
                    appmod.jobs.pop(job_id, None)

    def test_unavailable_subtitle_reports_error(self):
        job_id = 'test-subtitle-missing'
        appmod.jobs[job_id] = {'quality': 'subtitle:manual:ko'}
        try:
            with tempfile.TemporaryDirectory() as tmp, \
                 patch.object(appmod, 'get_download_dir', return_value=Path(tmp)), \
                 patch.object(appmod.yt_dlp, 'YoutubeDL') as factory, \
                 patch.object(appmod, 'history_update'):
                ydl = factory.return_value.__enter__.return_value
                ydl.extract_info.return_value = {'title': 'No subtitles'}
                appmod.run_download(job_id, 'https://example.com/watch', 'subtitle:manual:ko')
                self.assertEqual(appmod.jobs[job_id]['status'], 'error')
                ydl.process_ie_result.assert_not_called()
        finally:
            appmod.jobs.pop(job_id, None)


if __name__ == '__main__':
    unittest.main()
