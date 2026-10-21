from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from apps.accounts.models import User
from apps.qa.disclaimer import DISCLAIMER_CONTENT_HASH, DISCLAIMER_VERSION
from apps.qa.models import AIDisclaimerAcceptance, Answer, AnswerSource, Question, VoiceConversation

class QuestionViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="vet", email="vet@example.com", password="safe-test-password")
        AIDisclaimerAcceptance.objects.create(
            user=self.user,
            disclaimer_version=DISCLAIMER_VERSION,
            content_hash=DISCLAIMER_CONTENT_HASH,
            source=AIDisclaimerAcceptance.Source.TEXT,
        )
        self.client.force_login(self.user)

    def test_dashboard_is_private(self):
        self.client.logout()
        response = self.client.get(reverse("qa:dashboard"))
        self.assertEqual(response.status_code, 302)

    @override_settings(VOICE_FEATURE_ENABLED=True, VOICE_TEST_USERS={"vet@example.com"})
    def test_voice_switch_is_shown_to_authorized_user(self):
        response = self.client.get(reverse("qa:dashboard"))

        self.assertContains(response, reverse("qa:voice_dashboard"))
        self.assertContains(response, "Assistant input mode")

    @override_settings(VOICE_FEATURE_ENABLED=True, VOICE_TEST_USERS=set())
    def test_voice_switch_is_hidden_from_unauthorized_user(self):
        response = self.client.get(reverse("qa:dashboard"))

        self.assertNotContains(response, reverse("qa:voice_dashboard"))

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

    def test_history_separates_text_questions_from_grouped_voice_conversations(self):
        text_question = Question.objects.create(user=self.user, text="Typed TPLO question")
        conversation = VoiceConversation.objects.create(
            user=self.user,
            status=VoiceConversation.Status.ENDED,
        )
        first = Question.objects.create(
            user=self.user,
            text="First voice question",
            voice_conversation=conversation,
            voice_turn_index=1,
        )
        Question.objects.create(
            user=self.user,
            text="Second voice question",
            voice_conversation=conversation,
            voice_turn_index=2,
        )

        response = self.client.get(reverse("qa:history"))

        self.assertContains(response, "Voice conversations")
        self.assertContains(response, "2 questions")
        self.assertContains(response, "First voice question")
        self.assertContains(response, "Typed TPLO question")
        self.assertContains(
            response,
            reverse("qa:voice_conversation_detail", args=[conversation.pk]),
        )
        self.assertNotContains(response, reverse("qa:detail", args=[first.pk]))
        self.assertEqual(list(response.context["questions"]), [text_question])

    def test_voice_conversation_detail_orders_turns_and_answers(self):
        conversation = VoiceConversation.objects.create(user=self.user)
        second = Question.objects.create(
            user=self.user,
            text="Second saved turn",
            voice_conversation=conversation,
            voice_turn_index=2,
        )
        first = Question.objects.create(
            user=self.user,
            text="First saved turn",
            voice_conversation=conversation,
            voice_turn_index=1,
        )
        Answer.objects.create(
            question=first,
            text="First answer",
            structured_content={"summary": "First answer summary"},
            source_type=Answer.SourceType.CLIENT,
        )
        Answer.objects.create(
            question=second,
            text="Second answer",
            source_type=Answer.SourceType.AI_AUTO,
        )

        response = self.client.get(
            reverse("qa:voice_conversation_detail", args=[conversation.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "First answer summary")
        self.assertContains(response, "Second answer")
        content = response.content.decode()
        self.assertLess(content.index("First saved turn"), content.index("Second saved turn"))

    def test_user_cannot_read_another_users_voice_conversation(self):
        other = User.objects.create_user(
            username="voice-other",
            email="voice-other@example.com",
            password="safe-test-password",
        )
        conversation = VoiceConversation.objects.create(user=other)

        response = self.client.get(
            reverse("qa:voice_conversation_detail", args=[conversation.pk])
        )

        self.assertEqual(response.status_code, 404)

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

    def test_journal_source_renders_page_citation_and_clickable_inline_reference(self):
        question = Question.objects.create(user=self.user, text="What does the article report?", status=Question.Status.COMPLETE)
        answer = Answer.objects.create(
            question=question,
            text="Supported by the article [Source 2].",
            source_type=Answer.SourceType.CLIENT,
            structured_content={
                "summary": "Supported by the article [Source 2].",
                "sections": [], "limitations": [], "cited_sources": [2],
            },
        )
        AnswerSource.objects.create(
            answer=answer, chunk_id=101, source_document_id=55,
            source_type="journal_article", source_id="article-1",
            source_title="Laryngeal surgery study",
            source_url="https://doi.org/10.1000/example",
            chunk_index=4, similarity=.88, excerpt="Article evidence",
            citation_order=2,
            citation_metadata={
                "authors": "Example A, Example B", "journal": "Veterinary Surgery",
                "publication_year": 2014, "volume": "43", "issue": "6",
                "article_pages": "602-606", "doi": "10.1000/example",
                "pdf_page_start": 3, "printed_page_start": "604",
            },
        )

        response = self.client.get(reverse("qa:detail", args=[question.pk]))

        self.assertContains(response, f'href="#answer-{answer.pk}-source-2"')
        self.assertContains(response, 'id="answer-{}-source-2"'.format(answer.pk))
        self.assertContains(response, "Journal article")
        self.assertContains(response, "Example A, Example B")
        self.assertContains(response, "PDF page 3")
        self.assertContains(response, "Printed page 604")
        self.assertContains(response, "Open article")

    def test_unknown_inline_reference_is_not_linked_and_model_html_is_escaped(self):
        question = Question.objects.create(user=self.user, text="Unsafe formatting", status=Question.Status.COMPLETE)
        Answer.objects.create(
            question=question,
            text="Unsafe",
            source_type=Answer.SourceType.CLIENT,
            structured_content={
                "summary": '<script>alert("x")</script> [Source 9]',
                "sections": [], "limitations": [], "cited_sources": [],
            },
        )

        response = self.client.get(reverse("qa:detail", args=[question.pk]))

        self.assertNotContains(response, '<script>alert("x")</script>')
        self.assertContains(response, '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;', html=False)
        self.assertNotContains(response, "inline-citation")
