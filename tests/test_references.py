"""레퍼 체크 채널 저장과 쇼츠 필터링 API."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as appmod


CHANNEL = {"id": "UC_TEST", "name": "테스트 채널", "url": "https://www.youtube.com/channel/UC_TEST", "added_at": "2026-09-10T10:00:00"}


class ReferenceApiTest(unittest.TestCase):
    def setUp(self):
        self.client = appmod.app.test_client()
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "references.json"
        self.patch = patch.object(appmod, "REFERENCES_FILE", self.file)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_page_and_empty_list(self):
        self.assertEqual(self.client.get("/references").status_code, 200)
        self.assertEqual(self.client.get("/api/references").get_json(), {"items": []})

    def test_add_duplicate_and_delete(self):
        with patch.object(appmod, "extract_reference_channel", return_value=CHANNEL):
            r = self.client.post("/api/references", json={"url": "https://youtube.com/@test"})
            self.assertEqual(r.status_code, 201)
            self.assertEqual(self.client.post("/api/references", json={"url": "https://youtube.com/@test"}).status_code, 409)
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), [CHANNEL])
        self.assertEqual(self.client.delete("/api/references/UC_TEST").status_code, 200)
        self.assertEqual(self.client.delete("/api/references/UC_TEST").status_code, 404)

    def test_detail(self):
        self.file.write_text(json.dumps([CHANNEL]), encoding="utf-8")
        shorts = [{"id": "v", "view_count": 700_000}]
        with patch.object(appmod, "extract_reference_shorts", return_value=shorts):
            r = self.client.get("/api/references/UC_TEST")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["shorts"], shorts)
        self.assertEqual(r.get_json()["minimum_views"], 500_000)

    def test_invalid_url(self):
        r = self.client.post("/api/references", json={"url": "https://example.com/channel"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("유튜브", r.get_json()["error"])


class ReferenceDataTest(unittest.TestCase):
    def test_compact_short_pinned_comment(self):
        item = appmod._compact_short({
            "id": "abc", "title": "쇼츠", "view_count": 600_000,
            "comments": [{"text": "고정", "author": "채널", "is_pinned": True}],
        })
        self.assertEqual(item["pinned_comment"], {"text": "고정", "author": "채널"})
        self.assertEqual(item["view_count"], 600_000)


class ReferenceIncrementalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(appmod, "REFERENCES_FILE", Path(self.tmp.name) / "references.json")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def analyze(self, entries, fail=False):
        calls, states = [], []
        class Downloader:
            def __init__(self, opts):
                self.flat = opts.get("extract_flat")
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def extract_info(self, url, download=False):
                if self.flat:
                    return {"entries": iter(entries)}
                calls.append(url)
                if fail:
                    raise RuntimeError("temporary failure")
                return {"id": url.rsplit("/", 1)[-1], "title": "상세 제목", "view_count": 600000,
                        "description": "상세 설명"}
        with patch.object(appmod.yt_dlp, "YoutubeDL", Downloader):
            appmod.analyze_reference_shorts(CHANNEL, lambda s: states.append(json.loads(json.dumps(s))))
        return calls, states

    def test_saved_results_and_only_new_video_details(self):
        entries = [{"id": "old", "title": "기존", "view_count": 600000},
                   {"id": "small", "title": "작은 영상", "view_count": 100}]
        calls, states = self.analyze(entries)
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(s["stage"] == "listing" and len(s["shorts"]) == 2 for s in states))
        self.assertEqual(states[-1]["percent"], 100)
        self.assertEqual(len(appmod._load_reference_cache(CHANNEL)["items"]), 2)
        calls, states = self.analyze([{"id": "new", "title": "새 영상", "view_count": 900000}] + entries)
        self.assertEqual(calls, ["https://www.youtube.com/shorts/new"])
        self.assertEqual(len(states[0]["shorts"]), 2)
        self.assertEqual(len(states[-1]["shorts"]), 3)
        calls, _ = self.analyze([{"id": "new"}] + entries)
        self.assertEqual(calls, [])

    def test_failed_details_retry_from_file(self):
        entries = [{"id": "retry", "title": "재시도", "view_count": 600000}]
        _, states = self.analyze(entries, fail=True)
        self.assertEqual(states[-1]["shorts"][0]["detail_status"], "error")
        calls, states = self.analyze(entries)
        self.assertEqual(len(calls), 1)
        self.assertEqual(states[-1]["shorts"][0]["detail_status"], "complete")

    def test_incremental_scan_stops_before_older_pages(self):
        self.analyze([{"id": "old", "view_count": 10}])
        def entries():
            yield {"id": "new", "view_count": 10}
            yield {"id": "old", "view_count": 10}
            raise AssertionError("Should not fetch older pages")
        calls, states = self.analyze(entries())
        self.assertEqual(calls, [])
        self.assertEqual(len(states[-1]["shorts"]), 2)


if __name__ == "__main__":
    unittest.main()
