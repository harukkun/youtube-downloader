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
            **extra,
        }
        return self.client.post("/api/helper/recipe-description", json=body)

    def test_requests_missing_information_before_generation(self):
        llm_result = {
            "status": "needs_input",
            "questions": ["사용한 기름의 종류와 양을 알려주세요."],
            "instagram": "",
            "youtube": "",
            "tiktok": "",
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
        system, user, schema, _, _ = llm.call_args.args
        self.assertIn("[정보 확인 규칙]", system)
        self.assertIn("instagram", schema["required"])
        self.assertIn("youtube", schema["required"])
        self.assertIn("tiktok", schema["required"])
        self.assertIn("[INPUT]", user)

    def test_returns_separate_platform_results_with_followup_answers(self):
        instagram = "가" * 300
        youtube = "나" * 300
        tiktok = "다" * 300
        llm_result = {
            "status": "complete",
            "questions": [],
            "instagram": instagram,
            "youtube": youtube,
            "tiktok": tiktok,
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

    def test_adds_length_note_for_each_platform(self):
        llm_result = {
            "status": "complete",
            "questions": [],
            "instagram": "짧음",
            "youtube": "나" * 501,
            "tiktok": "다" * 501,
            "notes": [],
        }
        with patch.object(appmod, "llm_structured", return_value=llm_result):
            response = self.post()

        notes = response.get_json()["notes"]
        self.assertTrue(any("인스타그램" in note and "짧" in note for note in notes))
        self.assertTrue(any("유튜브" in note and "초과" in note for note in notes))
        self.assertTrue(any("틱톡" in note and "초과" in note for note in notes))

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
