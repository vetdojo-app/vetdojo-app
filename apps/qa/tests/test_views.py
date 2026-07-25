from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User
from apps.qa.models import Answer, Question

class QuestionViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="vet", email="vet@example.com", password="safe-test-password")
        self.client.force_login(self.user)

    def test_dashboard_is_private(self):
        self.client.logout()
        response = self.client.get(reverse("qa:dashboard"))
        self.assertEqual(response.status_code, 302)

    @patch("apps.qa.views.answer_question")
    def test_valid_question_is_created(self, mocked_answer):
        response = self.client.post(reverse("qa:dashboard"), {"question": "What are TPLO complications?"})
        question = Question.objects.get()
        self.assertRedirects(response, reverse("qa:detail", args=[question.pk]))
        mocked_answer.assert_called_once_with(question)

    def test_user_cannot_read_another_users_question(self):
        other = User.objects.create_user(username="other", email="other@example.com", password="safe-test-password")
        question = Question.objects.create(user=other, text="Private question")
        self.assertEqual(self.client.get(reverse("qa:detail", args=[question.pk])).status_code, 404)

    def test_ai_alternative_requires_library_answer(self):
        question = Question.objects.create(user=self.user, text="A clinical question", status=Question.Status.COMPLETE)
        Answer.objects.create(question=question, text="AI", source_type=Answer.SourceType.AI_AUTO)
        response = self.client.post(reverse("qa:ai_alternative", args=[question.pk]))
        self.assertEqual(response.status_code, 400)

    def test_existing_string_limitation_renders_as_single_item(self):
        question = Question.objects.create(user=self.user, text="A clinical question", status=Question.Status.COMPLETE)
        Answer.objects.create(
            question=question, text="AI answer", source_type=Answer.SourceType.AI_AUTO,
            structured_content={"summary": "AI answer", "sections": [], "limitations": "General principles only."},
        )
        response = self.client.get(reverse("qa:detail", args=[question.pk]))
        self.assertContains(response, "<li>General principles only.</li>", html=True)
        self.assertNotContains(response, "<li>G</li>", html=True)

    def test_legacy_character_list_renders_as_single_item(self):
        question = Question.objects.create(user=self.user, text="A legacy question", status=Question.Status.COMPLETE)
        Answer.objects.create(
            question=question, text="AI answer", source_type=Answer.SourceType.AI_REQUESTED,
            structured_content={"summary": "AI answer", "sections": [], "limitations": list("General principles only.")},
        )
        response = self.client.get(reverse("qa:detail", args=[question.pk]))
        self.assertContains(response, "<li>General principles only.</li>", html=True)
        self.assertNotContains(response, "<li>G</li>", html=True)

    def test_automatic_ai_answer_explains_library_was_insufficient(self):
        question = Question.objects.create(user=self.user, text="An unanswered question", status=Question.Status.COMPLETE)
        Answer.objects.create(question=question, text="AI answer", source_type=Answer.SourceType.AI_AUTO)
        response = self.client.get(reverse("qa:detail", args=[question.pk]))
        self.assertContains(response, "No sufficiently relevant answer was found in the active VetDojo knowledge base")

    def test_legacy_markdown_markers_are_cleaned_when_rendered(self):
        question = Question.objects.create(user=self.user, text="A formatting question", status=Question.Status.COMPLETE)
        Answer.objects.create(
            question=question, text="Fallback", source_type=Answer.SourceType.AI_AUTO,
            structured_content={
                "summary": "**Summary text**",
                "sections": [{"heading": "**Diagnoses**", "content": "1. **Renal Disease:** Details"}],
                "limitations": ["**General limitation:** Educational only"],
            },
        )
        response = self.client.get(reverse("qa:detail", args=[question.pk]))
        self.assertContains(response, "1. Renal Disease: Details")
        self.assertNotContains(response, "**Renal Disease:**")
