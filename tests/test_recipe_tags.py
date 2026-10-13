"""Tag metadata stays separate from captions and respects copy/paste limits."""
import unittest
from unittest.mock import patch

import app as m


class RecipeTagsTest(unittest.TestCase):
    def test_normalizes_without_inventing_keywords(self):
        tags, trimmed = m.normalize_youtube_tags([
            " #김치볶음밥 ", "김치볶음밥", "김치볶음밥  레시피",
            "KFood, kfood\n＃집밥", "", None, 123,
        ])
        self.assertEqual(tags, ["김치볶음밥", "김치볶음밥 레시피", "KFood", "집밥"])
        self.assertFalse(trimmed)

    def test_limit_includes_commas_and_implicit_quotes(self):
        tags = ["가" * 492, "밥 볶음"]
        self.assertEqual(m.youtube_tag_length(tags), 499)
        self.assertEqual(m.youtube_tag_length(["가" * 493, "밥 볶음"]), 500)
        self.assertEqual(m.normalize_youtube_tags(["가" * 493, "밥 볶음"]), (["가" * 493, "밥 볶음"], False))
        self.assertEqual(m.normalize_youtube_tags(["가" * 494, "밥 볶음"]), (["가" * 494], True))
        self.assertEqual(m.normalize_youtube_tags(["가" * 501]), ([], True))
        self.assertEqual(m.youtube_tag_length(["🍚 밥"]), 5)

    def test_extracts_unicode_hashtags_in_order_without_url_fragments(self):
        text = "#알쿡 #김치볶음밥, #집밥! #알쿡 #KFood #kfood #한식 #한식 https://x/#no 단어#no ##no #밥_1"
        self.assertEqual(m.extract_recipe_hashtags(text), ["#알쿡", "#김치볶음밥", "#집밥", "#KFood", "#한식", "#밥_1"])

    def post(self, result):
        with patch.object(m, "llm_structured", return_value=result), patch.object(m, "get_recipe_settings", side_effect=m.recipe_defaults):
            return m.app.test_client().post('/api/helper/recipe-description', json={
                "source_text": "김치 100g 밥 1공기 볶음", "source_channel": "요리 채널",
            })

    def test_requested_defaults_and_combinations(self):
        self.assertEqual(m.default_recipe_tags('김치볶음밥', '백종원'), [
            '알쿡', '알쿡레시피', 'shorts', '김치볶음밥 만드는법', '김치볶음밥 레시피',
            '김치볶음밥 만드는방법', '김치볶음밥 맛있게', '백종원 레시피',
        ])
        defaults = m.default_recipe_tags('토마토 달걀 볶음', '요리 채널!')
        original = '본문\n#알쿡 #Shorts #요리채널레시피'
        completed = m.add_default_recipe_hashtags(original, defaults)
        self.assertIn('#토마토달걀볶음만드는방법', completed)
        self.assertEqual(completed.count('#Shorts'), 1)
        self.assertNotIn('#shorts', completed)
        self.assertEqual(completed.count('#요리채널레시피'), 1)
        self.assertEqual(m.add_default_recipe_hashtags(completed, defaults), completed)

    def test_no_dish_name_does_not_invent_combinations(self):
        response = self.post(dict(status='complete', questions=[], instagram='가' * 300,
                                 youtube='나' * 300, tiktok='다' * 300, notes=[]))
        self.assertEqual(response.json['tags']['youtube'], ['알쿡', '알쿡레시피', 'shorts', '요리 채널 레시피'])
        self.assertTrue(any('요리명을 확인하지 못해' in note for note in response.json['notes']))

    def test_response_keeps_body_and_tag_fields_separate(self):
        result = dict(status="complete", questions=[], instagram="가" * 300,
                      youtube="나" * 300, tiktok="다" * 300 + "\n#알쿡 #집밥 #알쿡",
                      dish_name="김치볶음밥", recipe_author="백종원",
                      youtube_tags=["#김치볶음밥", "김치볶음밥", "가" * 500], notes=[])
        response = self.post(result)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['results']['youtube'], result['youtube'])
        defaults = m.default_recipe_tags('김치볶음밥', '백종원')
        self.assertEqual(response.json['tags']['youtube'], defaults + ['김치볶음밥'])
        self.assertEqual(response.json['tags']['tiktok'], ['#알쿡', '#집밥'] + ['#' + tag.replace(' ', '') for tag in defaults[1:]])
        self.assertEqual(response.json['tags']['tiktok'], m.extract_recipe_hashtags(response.json['results']['tiktok']))
        self.assertIn('백종원 레시피', response.json['tags']['youtube'])
        self.assertNotIn('요리 채널 레시피', response.json['tags']['youtube'])
        self.assertTrue(any('우선순위' in note for note in response.json['notes']))

    def test_missing_or_malformed_tags_do_not_discard_valid_posts(self):
        for tags in (None, "not an array", [None, " "]):
            response = self.post(dict(status="complete", questions=[], instagram="가" * 300,
                                     youtube="나" * 300, tiktok="다" * 300, dish_name="김치볶음밥", recipe_author="", youtube_tags=tags, notes=[]))
            self.assertEqual(response.status_code, 200)
            defaults = m.default_recipe_tags('김치볶음밥', '요리 채널')
            self.assertEqual(response.json['tags']['youtube'], defaults)
            self.assertEqual(response.json['tags']['tiktok'], ['#' + tag.replace(' ', '') for tag in defaults])
            self.assertEqual(response.json['notes'], [])


if __name__ == '__main__':
    unittest.main()
