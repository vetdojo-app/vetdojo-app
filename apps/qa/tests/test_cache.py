from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.qa.models import AIAnswerCache, Answer, Question
from apps.qa.services import answer_question, normalize_cache_question


@override_settings(OPENAI_CHAT_MODEL="test-model", AI_PROMPT_VERSION="test-v1", AI_CACHE_TTL_DAYS=30)
class AIAnswerCacheTests(TestCase):
    def setUp(self):
        self.first_user = User.objects.create_user(username="first", email="first@example.com", password="password")
        self.second_user = User.objects.create_user(username="second", email="second@example.com", password="password")

    def test_normalization_ignores_case_space_and_trailing_punctuation(self):
        self.assertEqual(normalize_cache_question("  What IS TPLO?  "), normalize_cache_question("what is tplo"))

    @patch("apps.qa.services.generate_content")
    @patch("apps.qa.services.retrieve_chunks", return_value=[])
    def test_library_is_checked_for_every_user_and_ai_generation_is_reused(self, retrieve, generate):
        content = {
            "evidence_sufficient": True, "summary": "Cached response", "sections": [],
            "limitations": [], "cited_sources": [],
        }
        generate.return_value = (content, 100, 50)
        first = Question.objects.create(user=self.first_user, text="What is TPLO?")
        second = Question.objects.create(user=self.second_user, text="  what IS tplo  ")

        first_answer = answer_question(first)
        second_answer = answer_question(second)

        self.assertEqual(retrieve.call_count, 2)
        self.assertEqual(generate.call_count, 1)
        self.assertNotEqual(first_answer.pk, second_answer.pk)
        self.assertEqual(first_answer.source_type, Answer.SourceType.AI_AUTO)
        self.assertEqual(second_answer.source_type, Answer.SourceType.AI_AUTO)
        self.assertEqual(second_answer.text, first_answer.text)
        cached = AIAnswerCache.objects.get()
        cached.refresh_from_db()
        self.assertEqual(cached.reuse_count, 1)
        self.assertEqual(second_answer.input_tokens, 0)

