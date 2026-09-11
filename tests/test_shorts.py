"""쇼츠 현황판: 시트 CSV 파싱과 썸네일 등록 API."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as appmod

HEADER_GROUP = "📋 기본,,🎬 원본 영상,,,,🔎 참고 쇼츠,,✍️ 내 영상 정보,,🗂️ 기타,,"
HEADER = "📌 상태,🍳 요리 제목,🔗 원본 링크,🎞️ 원본 제목,📺 원본 채널,🖼️ 썸네일,🔍 참고 쇼츠 링크,👥 참고 채널,✏️ 영상 제목,📝 설명,🕒 수정일,📅 등록일,🔗 썸네일 링크"
ROW = "🎬 제작 중,김치볶음밥,https://www.youtube.com/watch?v=kRl5OlSq7Sw,원본,채널,,,,내 제목,설명,2026-09-10 10:00,2026-09-01 09:00,https://lh3.googleusercontent.com/d/FILE123"

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class ParseTest(unittest.TestCase):
    def test_thumbnail_from_hidden_column(self):
        items = appmod.parse_sheet_items("\n".join([HEADER_GROUP, HEADER, ROW]))
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["row"], 3)
        self.assertEqual(it["video"]["thumbnail"], "https://lh3.googleusercontent.com/d/FILE123")
        self.assertEqual(it["source"]["thumbnail"], "https://i.ytimg.com/vi/kRl5OlSq7Sw/hqdefault.jpg")

    def test_status_labels_new_and_legacy(self):
        rows = {"⭐ 촬영 후보": "candidate", "⭐ 후보": "candidate", "⬜ 제작 전": "candidate", "🎬 촬영 중": "making", "🎬 제작 중": "making",
                "✂️ 편집 중": "editing", "⏳ 업로드 대기": "ready", "제작 완료·업로드 대기": "ready", "✅ 업로드 완료": "uploaded"}
        for label, key in rows.items():
            items = appmod.parse_sheet_items("\n".join([HEADER_GROUP, HEADER, label + "," + ROW.split(",", 1)[1]]))
            self.assertEqual(items[0]["status"], key, label)
        self.assertEqual(list(appmod.STATUSES), ["candidate", "making", "editing", "ready", "uploaded"])

    def test_old_sheet_without_column(self):
        header = HEADER.rsplit(",", 1)[0]
        row = ROW.rsplit(",", 1)[0]
        items = appmod.parse_sheet_items("\n".join([HEADER_GROUP, header, row]))
        self.assertEqual(items[0]["video"]["thumbnail"], "")

    def test_sniff_image(self):
        self.assertEqual(appmod.sniff_image(JPEG), "image/jpeg")
        self.assertEqual(appmod.sniff_image(PNG), "image/png")
        self.assertEqual(appmod.sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "image/webp")
        self.assertIsNone(appmod.sniff_image(b"GIF89a"))


SHEET = {"url": "https://docs.google.com/spreadsheets/d/abc/edit#gid=0", "sheet_id": "abc", "gid": "0"}
UPLOADER = {"url": "https://script.google.com/macros/s/AKfycbx_test/exec", "token": "tok"}


class ThumbnailUploadTest(unittest.TestCase):
    def setUp(self):
        self.client = appmod.app.test_client()

    def post(self, data=None, **form):
        body = {"row": "5", "src_url": "https://www.youtube.com/watch?v=kRl5OlSq7Sw", "dish": "김치볶음밥", **form}
        if data is not None:
            body["file"] = (io.BytesIO(data), "thumb.jpg")
        return self.client.post("/api/shorts/thumbnail", data=body, content_type="multipart/form-data")

    def test_ok(self):
        appmod._sheet_cache.update({"key": ("abc", "0"), "at": 12345.0, "items": [], "gid": "0"})
        with patch.object(appmod, "get_sheet_setting", return_value=SHEET), \
             patch.object(appmod, "get_upload_setting", return_value=UPLOADER), \
             patch.object(appmod, "apps_script_post", return_value={"ok": True, "row": 5, "url": "https://lh3.googleusercontent.com/d/X"}) as post:
            r = self.post(JPEG)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json(), {"ok": True, "row": 5, "url": "https://lh3.googleusercontent.com/d/X"})
        url, payload = post.call_args.args
        self.assertEqual(url, UPLOADER["url"])
        self.assertEqual(payload["action"], "thumbnail")
        self.assertEqual(payload["token"], "tok")
        self.assertEqual(payload["row"], 5)
        self.assertEqual(payload["mime"], "image/jpeg")
        self.assertEqual(payload["srcUrl"], "https://www.youtube.com/watch?v=kRl5OlSq7Sw")
        import base64
        self.assertEqual(base64.b64decode(payload["data"]), JPEG)
        self.assertEqual(appmod._sheet_cache["at"], 0.0)   # 캐시 무효화

    def test_validation(self):
        with patch.object(appmod, "get_sheet_setting", return_value=SHEET), \
             patch.object(appmod, "get_upload_setting", return_value=UPLOADER), \
             patch.object(appmod, "apps_script_post") as post:
            self.assertEqual(self.post(JPEG, row="2").status_code, 400)
            self.assertEqual(self.post(JPEG, row="x").status_code, 400)
            self.assertEqual(self.post(None).status_code, 400)
            self.assertEqual(self.post(b"GIF89a" + b"0" * 20).status_code, 400)
            post.assert_not_called()

    def test_unconfigured(self):
        with patch.object(appmod, "get_sheet_setting", return_value=None), \
             patch.object(appmod, "get_upload_setting", return_value=None):
            self.assertEqual(self.post(JPEG).status_code, 400)
        with patch.object(appmod, "get_sheet_setting", return_value=SHEET), \
             patch.object(appmod, "get_upload_setting", return_value=None):
            r = self.post(JPEG)
        self.assertEqual(r.status_code, 400)
        self.assertIn("업로드", r.get_json()["error"])

    def test_apps_script_errors(self):
        cases = {"row_mismatch": 409, "unauthorized": 401, "busy": 503, "weird": 502}
        for code, status in cases.items():
            with patch.object(appmod, "get_sheet_setting", return_value=SHEET), \
                 patch.object(appmod, "get_upload_setting", return_value=UPLOADER), \
                 patch.object(appmod, "apps_script_post", return_value={"ok": False, "error": code}):
                r = self.post(PNG)
            self.assertEqual(r.status_code, status, code)
            self.assertIn("error", r.get_json())
        with patch.object(appmod, "get_sheet_setting", return_value=SHEET), \
             patch.object(appmod, "get_upload_setting", return_value=UPLOADER), \
             patch.object(appmod, "apps_script_post", side_effect=RuntimeError("연결 실패")):
            r = self.post(PNG)
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.get_json()["error"], "연결 실패")


class CandidateApiTest(unittest.TestCase):
    def setUp(self):
        self.client = appmod.app.test_client()

    def configured(self, response):
        return patch.multiple(appmod, get_sheet_setting=lambda: SHEET,
                              get_upload_setting=lambda: UPLOADER,
                              apps_script_post=lambda _url, _payload: response)

    def test_list(self):
        response = {"ok": True, "items": [{"videoId": "kRl5OlSq7Sw", "status": "촬영 후보", "row": 3}]}
        with self.configured(response):
            r = self.client.get("/api/shorts/candidates")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["items"][0]["status"], "촬영 후보")

    def test_add_sends_sheet_and_video_metadata(self):
        appmod._sheet_cache["at"] = 123
        with patch.object(appmod, "get_sheet_setting", return_value=SHEET), \
             patch.object(appmod, "get_upload_setting", return_value=UPLOADER), \
             patch.object(appmod, "apps_script_post", return_value={"ok": True, "item": {"row": 3}}) as post:
            r = self.client.put("/api/shorts/candidates/kRl5OlSq7Sw", json={
                "url": "https://youtube.com/shorts/kRl5OlSq7Sw", "title": "원본", "channel": "채널"})
        self.assertEqual(r.status_code, 200)
        payload = post.call_args.args[1]
        self.assertEqual(payload["action"], "candidate_add")
        self.assertEqual(payload["sheetId"], "abc")
        self.assertEqual(payload["videoId"], "kRl5OlSq7Sw")
        self.assertEqual(payload["dishTitle"], "원본")
        self.assertEqual(payload["referenceChannel"], "채널")
        self.assertEqual(payload["referenceUrl"], "https://youtube.com/shorts/kRl5OlSq7Sw")
        self.assertNotIn("title", payload)
        self.assertNotIn("channel", payload)
        self.assertNotIn("url", payload)
        self.assertEqual(appmod._sheet_cache["at"], 0.0)

    def test_remove_and_locked_error(self):
        with self.configured({"ok": True, "removed": True}):
            self.assertEqual(self.client.delete("/api/shorts/candidates/kRl5OlSq7Sw").status_code, 200)
        with self.configured({"ok": False, "error": "not_candidate"}):
            r = self.client.delete("/api/shorts/candidates/kRl5OlSq7Sw")
        self.assertEqual(r.status_code, 409)
        self.assertIn("제작 단계", r.get_json()["error"])

    def test_requires_connections_and_reports_old_script(self):
        with patch.object(appmod, "get_sheet_setting", return_value=None):
            self.assertEqual(self.client.get("/api/shorts/candidates").status_code, 400)
        with self.configured({"ok": False, "error": "unknown_action"}):
            r = self.client.get("/api/shorts/candidates")
        self.assertEqual(r.status_code, 409)
        self.assertIn("최신 버전", r.get_json()["error"])


class UploaderConfigTest(unittest.TestCase):
    def setUp(self):
        self.client = appmod.app.test_client()
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / "config.json"
        self.config.write_text(json.dumps({"shorts_sheet_url": SHEET["url"]}), encoding="utf-8")
        self.patch = patch.object(appmod, "CONFIG_FILE", self.config)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def saved(self):
        return json.loads(self.config.read_text(encoding="utf-8"))

    def test_bad_url(self):
        r = self.client.post("/api/shorts/uploader", json={"url": "https://example.com/x", "token": "t"})
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("shorts_upload_url", self.saved())

    def test_missing_token(self):
        r = self.client.post("/api/shorts/uploader", json={"url": UPLOADER["url"], "token": ""})
        self.assertEqual(r.status_code, 400)

    def test_save_and_clear(self):
        with patch.object(appmod, "apps_script_post", return_value={"ok": True, "ping": True}) as post:
            r = self.client.post("/api/shorts/uploader", json={"url": UPLOADER["url"], "token": "tok"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(post.call_args.args[1], {"action": "ping", "token": "tok"})
        self.assertEqual(self.saved()["shorts_upload_token"], "tok")
        self.assertTrue(r.get_json()["sheet"]["upload_configured"])
        self.assertNotIn("tok", json.dumps(r.get_json()))   # 토큰은 응답에 나오지 않는다
        # 토큰 칸을 비우고 URL 만 다시 저장 → 저장된 토큰 재사용
        with patch.object(appmod, "apps_script_post", return_value={"ok": True}) as post:
            r = self.client.post("/api/shorts/uploader", json={"url": UPLOADER["url"], "token": ""})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(post.call_args.args[1]["token"], "tok")
        # 틀린 토큰
        with patch.object(appmod, "apps_script_post", return_value={"ok": False, "error": "unauthorized"}):
            r = self.client.post("/api/shorts/uploader", json={"url": UPLOADER["url"], "token": "bad"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.saved()["shorts_upload_token"], "tok")
        # 해제
        r = self.client.post("/api/shorts/uploader", json={"url": ""})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("shorts_upload_url", self.saved())
        self.assertFalse(r.get_json()["sheet"]["upload_configured"])


class RecentThumbTest(unittest.TestCase):
    """CSV 내보내기가 늦게 반영되는 동안 방금 등록한 썸네일을 덧씌운다."""

    def setUp(self):
        appmod._recent_thumbs.clear()

    def item(self, row, src, thumb=""):
        return {"row": row, "source": {"url": src}, "video": {"thumbnail": thumb}}

    def test_overlay_until_sheet_catches_up(self):
        appmod.remember_recent_thumb(5, "https://lh3.googleusercontent.com/d/NEW", "https://youtu.be/a")
        out = appmod.apply_recent_thumbs([self.item(5, "https://youtu.be/a"), self.item(6, "https://youtu.be/b")])
        self.assertEqual(out[0]["video"]["thumbnail"], "https://lh3.googleusercontent.com/d/NEW")
        self.assertEqual(out[1]["video"]["thumbnail"], "")
        # 시트가 따라오면 기억을 지운다
        appmod.apply_recent_thumbs([self.item(5, "https://youtu.be/a", "https://lh3.googleusercontent.com/d/NEW")])
        self.assertNotIn(5, appmod._recent_thumbs)

    def test_row_shifted_not_overlaid(self):
        appmod.remember_recent_thumb(5, "https://x/NEW", "https://youtu.be/a")
        out = appmod.apply_recent_thumbs([self.item(5, "https://youtu.be/OTHER")])
        self.assertEqual(out[0]["video"]["thumbnail"], "")

    def test_expired(self):
        appmod.remember_recent_thumb(5, "https://x/NEW", "")
        appmod._recent_thumbs[5]["at"] -= appmod.RECENT_THUMB_TTL + 1
        out = appmod.apply_recent_thumbs([self.item(5, "")])
        self.assertEqual(out[0]["video"]["thumbnail"], "")
        self.assertNotIn(5, appmod._recent_thumbs)


if __name__ == "__main__":
    unittest.main()
