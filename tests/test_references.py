"""레퍼 체크 채널 저장, 쇼츠 필터링 API, 구글 시트 팀 동기화."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as appmod


CHANNEL = {"id": "UC_TEST", "name": "테스트 채널", "url": "https://www.youtube.com/channel/UC_TEST", "added_at": "2026-09-10T10:00:00"}
SHEET = {"url": "https://docs.google.com/spreadsheets/d/abc/edit#gid=0", "sheet_id": "abc", "gid": "0"}
UPLOADER = {"url": "https://script.google.com/macros/s/AKfycbx_test/exec", "token": "tok"}


def run_analysis(entries, fail=False):
    """yt-dlp 를 가짜로 바꿔 분석을 돌린다 → (상세 조회한 URL 목록, publish 된 상태 목록)."""
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


class LocalOnlyMixin:
    """시트 미연결 상태: 실제 ~/.youtube-downloader/config.json 을 읽어 네트워크에 나가지 않게 막는다."""

    def start_local(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "references.json"
        self.patches = [patch.object(appmod, "REFERENCES_FILE", self.file),
                        patch.object(appmod, "get_sheet_setting", return_value=None),
                        patch.object(appmod, "get_upload_setting", return_value=None)]
        for p in self.patches:
            p.start()

    def stop_local(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()


class ReferenceApiTest(LocalOnlyMixin, unittest.TestCase):
    def setUp(self):
        self.client = appmod.app.test_client()
        self.start_local()

    def tearDown(self):
        self.stop_local()

    def test_page_and_empty_list(self):
        self.assertEqual(self.client.get("/references").status_code, 200)
        self.assertEqual(self.client.get("/api/references").get_json(),
                         {"items": [], "sync": {"enabled": False, "sheet_connected": False,
                                                "uploader_connected": False, "error": None}})
        self.assertEqual(self.client.get("/api/references/sync").get_json(),
                         {"enabled": False, "sheet_connected": False, "uploader_connected": False})

    def test_sync_status_reports_which_connection_is_missing(self):
        with patch.object(appmod, "get_sheet_setting", return_value=SHEET):
            self.assertEqual(self.client.get("/api/references/sync").get_json(),
                             {"enabled": False, "sheet_connected": True, "uploader_connected": False})

    def test_add_duplicate_and_delete(self):
        with patch.object(appmod, "extract_reference_channel", return_value=CHANNEL):
            r = self.client.post("/api/references", json={"url": "https://youtube.com/@test"})
            self.assertEqual(r.status_code, 201)
            self.assertIsNone(r.get_json()["sync_error"])
            self.assertEqual(self.client.post("/api/references", json={"url": "https://youtube.com/@test"}).status_code, 409)
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), [CHANNEL])
        cache = appmod._reference_cache_path(CHANNEL)
        cache.parent.mkdir(parents=True)
        cache.write_text("{}", encoding="utf-8")
        self.assertEqual(self.client.delete("/api/references/UC_TEST").status_code, 200)
        self.assertFalse(cache.exists(), "채널 삭제 시 로컬 분석 캐시도 지운다")
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

    def test_merge_prefers_completed_details_and_keeps_local_otherwise(self):
        local = {"a": {"id": "a", "detail_status": "pending"},
                 "b": {"id": "b", "detail_status": "complete", "title": "로컬"}}
        remote = [{"id": "a", "detail_status": "complete"},
                  {"id": "b", "detail_status": "complete", "title": "원격"},
                  {"id": "c", "detail_status": "skipped"},
                  {"title": "id 없음"}]
        self.assertEqual(appmod._merge_reference_items(local, remote), 2)
        self.assertEqual(local["a"]["detail_status"], "complete")
        self.assertEqual(local["b"]["title"], "로컬")
        self.assertEqual(local["c"]["detail_status"], "skipped")


class ReferenceIncrementalTest(LocalOnlyMixin, unittest.TestCase):
    def setUp(self):
        self.start_local()

    def tearDown(self):
        self.stop_local()

    def test_saved_results_and_only_new_video_details(self):
        entries = [{"id": "old", "title": "기존", "view_count": 600000},
                   {"id": "small", "title": "작은 영상", "view_count": 100}]
        calls, states = run_analysis(entries)
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(s["stage"] == "listing" and len(s["shorts"]) == 2 for s in states))
        self.assertEqual(states[-1]["percent"], 100)
        self.assertFalse(states[-1]["sync"])
        self.assertEqual(len(appmod._load_reference_cache(CHANNEL)["items"]), 2)
        calls, states = run_analysis([{"id": "new", "title": "새 영상", "view_count": 900000}] + entries)
        self.assertEqual(calls, ["https://www.youtube.com/shorts/new"])
        self.assertEqual(len(states[0]["shorts"]), 2)
        self.assertEqual(len(states[-1]["shorts"]), 3)
        calls, _ = run_analysis([{"id": "new"}] + entries)
        self.assertEqual(calls, [])

    def test_failed_details_retry_from_file(self):
        entries = [{"id": "retry", "title": "재시도", "view_count": 600000}]
        _, states = run_analysis(entries, fail=True)
        self.assertEqual(states[-1]["shorts"][0]["detail_status"], "error")
        calls, states = run_analysis(entries)
        self.assertEqual(len(calls), 1)
        self.assertEqual(states[-1]["shorts"][0]["detail_status"], "complete")

    def test_incremental_scan_stops_before_older_pages(self):
        run_analysis([{"id": "old", "view_count": 10}])
        def entries():
            yield {"id": "new", "view_count": 10}
            yield {"id": "old", "view_count": 10}
            raise AssertionError("Should not fetch older pages")
        calls, states = run_analysis(entries())
        self.assertEqual(calls, [])
        self.assertEqual(len(states[-1]["shorts"]), 2)


class ReferenceSyncTest(unittest.TestCase):
    """현황판 시트 + Apps Script 가 연결된 상태의 팀 동기화."""

    def setUp(self):
        self.client = appmod.app.test_client()
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "references.json"
        self.patches = [patch.object(appmod, "REFERENCES_FILE", self.file),
                        patch.object(appmod, "get_sheet_setting", return_value=SHEET),
                        patch.object(appmod, "get_upload_setting", return_value=UPLOADER)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def script(self, handler):
        """apps_script_post 를 handler(payload) 로 바꾼다. 보낸 payload 는 calls 에 쌓인다."""
        calls = []
        def post(_url, payload, timeout=None):
            calls.append(payload)
            return handler(payload)
        return patch.object(appmod, "apps_script_post", side_effect=post), calls

    def local(self):
        return json.loads(self.file.read_text(encoding="utf-8"))

    def test_list_pulls_from_sheet_and_caches_locally(self):
        remote = [dict(CHANNEL, name="시트 채널"), {"name": "id 없는 행"}]
        stub, calls = self.script(lambda b: {"ok": True, "items": remote})
        with stub:
            r = self.client.get("/api/references")
        self.assertEqual(r.get_json(), {"items": remote[:1], "sync": {
            "enabled": True, "sheet_connected": True, "uploader_connected": True, "error": None}})
        self.assertEqual(self.client.get("/api/references/sync").get_json(),
                         {"enabled": True, "sheet_connected": True, "uploader_connected": True})
        self.assertEqual(calls[0]["action"], "reference_list")
        self.assertEqual((calls[0]["sheetId"], calls[0]["token"]), ("abc", "tok"))
        self.assertEqual(self.local(), remote[:1])

    def test_list_falls_back_to_local_when_sheet_fails(self):
        self.file.write_text(json.dumps([CHANNEL]), encoding="utf-8")
        with patch.object(appmod, "apps_script_post", side_effect=RuntimeError("연결 실패")):
            r = self.client.get("/api/references")
        self.assertEqual(r.get_json()["items"], [CHANNEL])
        self.assertEqual(r.get_json()["sync"]["error"], "연결 실패")
        self.assertTrue(r.get_json()["sync"]["enabled"])

    def test_add_and_remove_push_to_sheet(self):
        stub, calls = self.script(lambda b: {"ok": True, "item": b.get("channel"), "existing": False, "removed": True})
        with stub, patch.object(appmod, "extract_reference_channel", return_value=CHANNEL):
            r = self.client.post("/api/references", json={"url": "https://youtube.com/@test"})
            self.assertEqual(r.status_code, 201)
            self.assertIsNone(r.get_json()["sync_error"])
            self.assertEqual(calls[-1]["action"], "reference_add")
            self.assertEqual(calls[-1]["channel"], CHANNEL)
            r = self.client.delete("/api/references/UC_TEST")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((calls[-1]["action"], calls[-1]["id"]), ("reference_remove", "UC_TEST"))
        self.assertEqual(self.local(), [])

    def test_add_keeps_local_and_reports_sync_error(self):
        with patch.object(appmod, "apps_script_post", return_value={"ok": False, "error": "unknown_action"}), \
             patch.object(appmod, "extract_reference_channel", return_value=CHANNEL):
            r = self.client.post("/api/references", json={"url": "https://youtube.com/@test"})
        self.assertEqual(r.status_code, 201)
        self.assertIn("최신 버전", r.get_json()["sync_error"])
        self.assertEqual(self.local(), [CHANNEL])

    def test_add_uses_sheet_copy_when_teammate_added_first(self):
        remote = dict(CHANNEL, name="팀원이 먼저 추가")
        stub, _ = self.script(lambda b: {"ok": True, "item": remote, "existing": True})
        with stub, patch.object(appmod, "extract_reference_channel", return_value=CHANNEL):
            r = self.client.post("/api/references", json={"url": "https://youtube.com/@test"})
        self.assertEqual(r.get_json()["item"], remote)
        self.assertEqual(self.local(), [remote])

    def test_analysis_pulls_team_results_then_pushes_only_changes(self):
        remote_short = {"id": "old", "title": "팀 분석", "view_count": 800000, "detail_status": "complete",
                        "url": "https://www.youtube.com/shorts/old"}
        def handler(body):
            if body["action"] == "reference_shorts_get":
                return {"ok": True, "items": [remote_short], "complete": True, "updated_at": "2026-09-10T09:00:00"}
            return {"ok": True, "updated": 0, "inserted": len(body["items"])}
        stub, calls = self.script(handler)
        entries = [{"id": "new", "title": "새 영상", "view_count": 900000},
                   {"id": "old", "title": "기존", "view_count": 800000},
                   {"id": "older", "title": "더 오래된", "view_count": 800000}]
        with stub:
            detail_calls, states = run_analysis(entries)
        # 팀원이 전체 목록을 이미 수집했으므로(complete) 알려진 영상에서 멈추고 새 영상만 상세 조회한다.
        self.assertEqual(detail_calls, ["https://www.youtube.com/shorts/new"])
        self.assertEqual(calls[0]["action"], "reference_shorts_get")
        self.assertEqual(calls[0]["channelId"], "UC_TEST")
        puts = [c for c in calls if c["action"] == "reference_shorts_put"]
        self.assertEqual(len(puts), 2, "목록 단계 끝과 상세 단계 끝에 한 번씩 올린다")
        self.assertEqual([v["id"] for v in puts[0]["items"]], ["new"])
        self.assertTrue(puts[0]["complete"])
        self.assertEqual([v["id"] for v in puts[1]["items"]], ["new"])
        self.assertEqual(puts[1]["items"][0]["detail_status"], "complete")
        self.assertGreater(puts[1]["updated_at"], "2026-09-10T09:00:00")
        last = states[-1]
        self.assertEqual((last["stage"], last["sync"], last["sync_error"]), ("complete", True, None))
        self.assertEqual({v["id"] for v in last["shorts"]}, {"new", "old"})
        self.assertEqual(appmod._load_reference_cache(CHANNEL)["items"]["old"]["title"], "팀 분석")

    def test_analysis_pushes_in_chunks(self):
        stub, calls = self.script(lambda b: {"ok": True, "items": [], "complete": False, "updated_at": None})
        entries = [{"id": f"v{i:03d}", "title": str(i), "view_count": 10} for i in range(appmod.REFERENCE_SYNC_CHUNK + 5)]
        with stub:
            run_analysis(entries)
        puts = [c for c in calls if c["action"] == "reference_shorts_put"]
        self.assertEqual([len(p["items"]) for p in puts], [appmod.REFERENCE_SYNC_CHUNK, 5, 0])
        self.assertNotIn("complete", puts[0])
        self.assertTrue(puts[1]["complete"])

    def test_analysis_completes_when_sheet_fails(self):
        with patch.object(appmod, "apps_script_post", side_effect=RuntimeError("연결 실패")):
            _, states = run_analysis([{"id": "v", "title": "t", "view_count": 600000}])
        last = states[-1]
        self.assertEqual(last["stage"], "complete")
        self.assertEqual(last["sync_error"], "연결 실패")
        self.assertIn("시트 동기화 실패: 연결 실패", last["message"])
        self.assertEqual(appmod._load_reference_cache(CHANNEL)["items"]["v"]["detail_status"], "complete")


if __name__ == "__main__":
    unittest.main()
