import json
"""SNS 레시피 게시글 생성 API와 정보 보완 흐름."""
import unittest
from unittest.mock import patch

import app as appmod


class RecipeHelperTest(unittest.TestCase):
    def setUp(self):
        self.client = appmod.app.test_client()

    def post(self, **extra):
        body = {
            "source_text": "김치볶음밥. 김치 100g과 밥 1공기를 3분간 볶는다.",
            "source_channel": "테스트 요리 채널",
            **extra,
        }
        return self.client.post("/api/helper/recipe-description", json=body)

    def test_requires_source_channel_before_calling_llm(self):
        with patch.object(appmod, "llm_structured") as llm:
            for channel in (None, "", "   "):
                self.assertEqual(self.post(source_channel=channel).status_code, 400)
            llm.assert_not_called()

    def test_legacy_codex_model_is_removed_from_choices(self):
        response = self.client.get("/upload-process")
        self.assertNotIn(b'gpt-5.5', response.data)
        self.assertIn(appmod.DEFAULT_CODEX_MODEL.encode(), response.data)

    def test_saved_legacy_model_keeps_custom_recipe_settings(self):
        saved = {**appmod.recipe_defaults(), "model": "gpt-5.5",
                 "template": "내 템플릿", "instructions": "내 지시사항"}
        with patch.object(appmod, "load_helper", return_value={"recipe": saved}):
            settings = appmod.get_recipe_settings()
        self.assertEqual(settings["model"], appmod.DEFAULT_CODEX_MODEL)
        self.assertEqual(settings["template"], saved["template"])
        self.assertEqual(settings["instructions"], saved["instructions"])

    def test_stale_browser_model_generates_with_default_codex_model(self):
        result = {"status": "needs_input", "questions": ["분량은 얼마인가요?"]}
        with patch.object(appmod, "_llm_via_codex", return_value=result) as codex:
            response = self.post(backend="codex", model="gpt-5.5")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(codex.call_args.args[-1], appmod.DEFAULT_CODEX_MODEL)

    def test_shared_llm_dispatch_replaces_legacy_codex_model(self):
        with patch.object(appmod, "_llm_via_codex", return_value={}) as codex:
            appmod.llm_structured("system", "user", {}, "codex", "gpt-5.5")
        codex.assert_called_once_with("system", "user", {}, appmod.DEFAULT_CODEX_MODEL)

    def test_unsupported_codex_model_error_omits_echoed_recipe(self):
        error = "개인 레시피 내용\nERROR: The 'fixture' model is not supported when using Codex with a ChatGPT account."
        proc = appmod.subprocess.CompletedProcess([], 1, stdout="", stderr=error)
        with patch.object(appmod, "find_codex_cli", return_value="/fake/codex"), \
                patch.object(appmod.subprocess, "run", return_value=proc):
            with self.assertRaises(RuntimeError) as caught:
                appmod._llm_via_codex("system", "user", {}, "fixture")
        self.assertIn("fixture 모델을 지원하지 않습니다", str(caught.exception))
        self.assertNotIn("개인 레시피", str(caught.exception))

    def test_requests_missing_information_before_generation(self):
        llm_result = {
            "status": "needs_input",
            "questions": ["사용한 기름의 종류와 양을 알려주세요."],
            "instagram": "",
            "youtube": "",
            "tiktok": "",
            "dish_name": "김치볶음밥",
            "recipe_author": "",
            "youtube_tags": ["김치볶음밥", "김치볶음밥 레시피"],
            "notes": [],
            "_usage": {"model": "test"},
        }
        with patch.object(appmod, "llm_structured", return_value=llm_result) as llm:
            response = self.post()

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "needs_input")
        self.assertEqual(data["questions"], llm_result["questions"])
        self.assertEqual(data["results"], {})
        self.assertEqual(data["tags"], {"youtube": [], "tiktok": []})
        system, user, schema, _, _ = llm.call_args.args
        self.assertIn("[정보 확인 규칙]", system)
        self.assertIn("instagram", schema["required"])
        self.assertIn("youtube", schema["required"])
        self.assertIn("tiktok", schema["required"])
        self.assertIn("youtube_tags", schema["required"])
        self.assertIn("틱톡의 이 규칙은", system)
        self.assertIn("[INPUT]", user)
        self.assertIn("( 테스트 요리 채널 )", user)
        self.assertIn("더 자세한 레시피 확인을 위해 원본 영상을 꼭 확인해주세요 :)", user)
        self.assertIn("음슴체", system)
        self.assertNotIn("결론은 👉", system)
        self.assertNotIn("결론은 👉", appmod.DEFAULT_RECIPE_TEMPLATE)
        self.assertIn("🛒 오늘 쓴 재료·도구\n- [제품명]: [쿠팡 링크]", appmod.DEFAULT_RECIPE_TEMPLATE)
        self.assertIn("쿠팡 파트너스 활동의 일환", appmod.DEFAULT_RECIPE_TEMPLATE)
        self.assertIn("대괄호 표시를 그대로 출력", system)
        self.assertIn("youtube 설명란에는 해시태그를 넣지 않습니다", system)

    def test_returns_separate_platform_results_with_followup_answers(self):
        instagram = "가" * 300
        youtube = "나" * 300
        tiktok = "다" * 300 + "\n" + " ".join("#" + tag.replace(" ", "") for tag in appmod.default_recipe_tags("김치볶음밥", "테스트 요리 채널"))
        llm_result = {
            "status": "complete",
            "questions": [],
            "instagram": instagram,
            "youtube": youtube,
            "tiktok": tiktok,
            "dish_name": "김치볶음밥",
            "recipe_author": "",
            "youtube_tags": ["김치볶음밥", "김치볶음밥 레시피"],
            "notes": [],
        }
        additional = [{"question": "기름 양은?", "answer": "식용유 1T"}]
        with patch.object(appmod, "llm_structured", return_value=llm_result) as llm:
            response = self.post(additional_info=additional)

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "complete")
        self.assertEqual(data["results"], {"instagram": instagram, "youtube": youtube, "tiktok": tiktok})
        self.assertEqual(data["notes"], [])
        user = llm.call_args.args[1]
        self.assertIn("[추가 정보 답변]", user)
        self.assertIn("질문: 기름 양은?\n답변: 식용유 1T", user)

    def test_removes_trailing_hashtags_from_youtube_description(self):
        body = "🍳 김치볶음밥 레시피\n1️⃣ #1 단계처럼 본문 속 기호는 유지함."
        llm_result = {
            "status": "complete",
            "questions": [],
            "instagram": "가" * 300 + "\n#알쿡 #김치볶음밥",
            "youtube": body + "\n\n#알쿡 #김치볶음밥\n#레시피",
            "tiktok": "다" * 300,
            "dish_name": "김치볶음밥",
            "recipe_author": "",
            "youtube_tags": [],
            "notes": [],
        }
        with patch.object(appmod, "llm_structured", return_value=llm_result):
            data = self.post().get_json()

        self.assertEqual(data["results"]["youtube"], body)
        self.assertTrue(data["results"]["instagram"].endswith("#알쿡 #김치볶음밥"))

    def coupang_result(self):
        block = ("🍳 김치볶음밥 레시피\n계기 👩🏻‍🍳\n\n🛒 오늘 쓴 재료·도구\n- [제품명]: [쿠팡 링크]\n- [제품명]: [쿠팡 링크]\n"
                 "※ 이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.\n\n🧑🏻‍🍳 만드는 법\n1️⃣ 볶음.")
        return {"status": "complete", "questions": [], "instagram": block, "youtube": block, "tiktok": block,
                "dish_name": "김치볶음밥", "recipe_author": "", "youtube_tags": [], "notes": []}

    def test_fills_coupang_links_without_sending_them_to_llm(self):
        links = "프라이팬: https://link.coupang.com/a/abc?x=1\n- 식용유 : https://link.coupang.com/a/def"
        with patch.object(appmod, "llm_structured", return_value=self.coupang_result()) as llm:
            data = self.post(product_links=links).get_json()

        youtube = data["results"]["youtube"]
        self.assertIn("🛒 오늘 쓴 재료·도구\n- 프라이팬: https://link.coupang.com/a/abc?x=1\n- 식용유: https://link.coupang.com/a/def\n※", youtube)
        self.assertNotIn("[제품명]", youtube)
        self.assertNotIn("link.coupang.com", llm.call_args.args[1])

    def test_removes_coupang_block_without_links(self):
        with patch.object(appmod, "llm_structured", return_value=self.coupang_result()):
            data = self.post().get_json()

        for text in data["results"].values():
            self.assertNotIn("🛒", text)
            self.assertNotIn("쿠팡", text)
            self.assertIn("계기 👩🏻‍🍳\n\n🧑🏻‍🍳 만드는 법", text)

    def test_rejects_coupang_line_without_url(self):
        with patch.object(appmod, "llm_structured") as llm:
            response = self.post(product_links="프라이팬")
        self.assertEqual(response.status_code, 400)
        llm.assert_not_called()

    def test_cli_auth_failure_explains_login(self):
        output = json.dumps({"type": "result", "is_error": True, "result": "Failed to authenticate: OAuth session expired and could not be refreshed"})
        proc = appmod.subprocess.CompletedProcess([], 1, stdout=output, stderr="")
        with patch.object(appmod.shutil, "which", return_value="/usr/bin/claude"), \
                patch.object(appmod.subprocess, "run", return_value=proc):
            with self.assertRaisesRegex(RuntimeError, "claude auth login"):
                appmod._llm_via_cli("system", "user", {}, "sonnet")

    def test_adds_length_note_for_each_platform(self):
        llm_result = {
            "status": "complete",
            "questions": [],
            "instagram": "가" * 2201,
            "youtube": "나" * 5000,
            "tiktok": "다" * 4001,
            "dish_name": "김치볶음밥",
            "recipe_author": "",
            "youtube_tags": ["김치볶음밥", "김치볶음밥 레시피"],
            "notes": [],
        }
        with patch.object(appmod, "llm_structured", return_value=llm_result):
            response = self.post()

        notes = response.get_json()["notes"]
        self.assertTrue(any("인스타그램" in note and "2,200자" in note for note in notes))
        self.assertFalse(any("유튜브" in note for note in notes))
        self.assertTrue(any("틱톡" in note and "4,000자" in note for note in notes))
        self.assertFalse(any("짧" in note for note in notes))

    def test_finds_chatgpt_bundled_codex_when_path_is_missing(self):
        bundled = "/Applications/ChatGPT.app/Contents/Resources/codex"
        with patch.dict(appmod.os.environ, {}, clear=True), \
                patch.object(appmod.shutil, "which", return_value=None), \
                patch.object(appmod.Path, "is_file", lambda path: str(path) == bundled), \
                patch.object(appmod.os, "access", return_value=True):
            self.assertEqual(appmod.find_codex_cli(), bundled)
            self.assertTrue(appmod.llm_environment()["codex_available"])

    def test_explicit_codex_path_requires_an_executable_file(self):
        with patch.dict(appmod.os.environ, {"CODEX_CLI_PATH": "/custom/codex"}), \
                patch.object(appmod.Path, "is_file", return_value=True), \
                patch.object(appmod.os, "access", return_value=True):
            self.assertEqual(appmod.find_codex_cli(), "/custom/codex")
            with patch.object(appmod.os, "access", return_value=False):
                self.assertIsNone(appmod.find_codex_cli())

    def test_finds_nested_desktop_codex_without_path(self):
        for root in (appmod.Path("/Applications"), appmod.Path.home() / "Applications"):
            for name in ("Codex.app", "ChatGPT.app"):
                bundled = str(root / name / "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex")
                with self.subTest(bundled=bundled), \
                        patch.dict(appmod.os.environ, {}, clear=True), \
                        patch.object(appmod.shutil, "which", return_value=None), \
                        patch.object(appmod.Path, "is_file", lambda path: str(path) == bundled), \
                        patch.object(appmod.os, "access", return_value=True):
                    self.assertEqual(appmod.find_codex_cli(), bundled)
                    self.assertTrue(appmod.llm_environment()["codex_available"])
                    with patch.object(appmod.os, "access", return_value=False):
                        self.assertIsNone(appmod.find_codex_cli())

    def test_rejects_invalid_additional_information(self):
        response = self.post(additional_info=[{}] * 13)
        self.assertEqual(response.status_code, 400)

    def test_helper_page_exposes_platform_and_question_ui(self):
        response = self.client.get("/helper")
        html = response.get_data(as_text=True)
        self.assertIn('data-platform="instagram"', html)
        self.assertIn('data-platform="youtube"', html)
        self.assertIn('data-platform="tiktok"', html)
        self.assertIn('id="clarifyBox"', html)


if __name__ == "__main__":
    unittest.main()
