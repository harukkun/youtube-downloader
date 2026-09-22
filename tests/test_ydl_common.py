"""yt-dlp 공통 옵션: JS 챌린지 해결 스크립트 허용이 모든 조회·다운로드 경로에 붙는지 확인한다.

이 옵션이 빠지면 유튜브가 기본 플레이어 클라이언트를 막는 영상(아동용 표시 등)에서
쓸 수 있는 포맷이 하나도 남지 않아 "This video is not available" 로 실패한다.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as appmod
import yt_dlp
from hooks import media
from ydl_common import REMOTE_COMPONENTS, ydl_opts


class YdlOptsTest(unittest.TestCase):
    def test_adds_remote_components_without_touching_caller_dict(self):
        original = {"quiet": True}
        merged = ydl_opts(original)
        self.assertEqual(merged["remote_components"], REMOTE_COMPONENTS)
        self.assertTrue(merged["quiet"])
        self.assertNotIn("remote_components", original)

    def test_caller_can_override(self):
        self.assertEqual(ydl_opts({"remote_components": []})["remote_components"], [])

    def test_component_is_supported_by_installed_yt_dlp(self):
        # yt-dlp 가 지원 목록에 없는 값을 받으면 경고만 내고 조용히 버리므로 여기서 막는다.
        with yt_dlp.YoutubeDL({"quiet": True, "remote_components": list(REMOTE_COMPONENTS)}) as ydl:
            self.assertEqual(set(ydl.params["remote_components"]), set(REMOTE_COMPONENTS))


class CallSiteTest(unittest.TestCase):
    """실제 호출부가 만드는 옵션에 공통 옵션이 들어 있는지 확인한다."""

    def collect(self, call):
        seen = []

        class Downloader:
            def __init__(self, opts):
                seen.append(opts)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=False):
                return {"id": "x", "title": "t", "formats": [], "channel_id": "UC_X"}

        with patch.object(yt_dlp, "YoutubeDL", Downloader):
            call()
        self.assertTrue(seen, "yt-dlp 가 호출되지 않았습니다.")
        for opts in seen:
            self.assertEqual(opts.get("remote_components"), REMOTE_COMPONENTS)

    def test_download_opts(self):
        with tempfile.TemporaryDirectory() as tmp:
            for quality in ("audio", "1080"):
                opts = appmod.build_ydl_opts(quality, Path(tmp))
                self.assertEqual(opts["remote_components"], REMOTE_COMPONENTS)

    def test_info_endpoint(self):
        def call():
            appmod.app.test_client().post("/api/info", json={"url": "https://www.youtube.com/watch?v=abc"})

        self.collect(call)

    def test_reference_channel_lookup(self):
        self.collect(lambda: appmod.extract_reference_channel("https://www.youtube.com/@test"))

    def test_hooks_remote_info(self):
        self.collect(lambda: media.remote_info("https://www.youtube.com/watch?v=abcdefghijk"))


if __name__ == "__main__":
    unittest.main()
