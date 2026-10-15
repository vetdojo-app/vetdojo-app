from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.qa.voice import VoiceConfigurationError, create_realtime_client_secret, safety_identifier
from apps.qa.models import Answer, AnswerSource, Question


User = get_user_model()


@override_settings(
    VOICE_FEATURE_ENABLED=True,
    OPENAI_API_KEY="test-key",
    OPENAI_REALTIME_MODEL="gpt-realtime",
    OPENAI_REALTIME_VOICE="marin",
    OPENAI_REALTIME_TRANSCRIPTION_MODEL="gpt-transcribe",
    VOICE_CLIENT_SECRET_TTL_SECONDS=60,
    VOICE_SESSION_REQUEST_COOLDOWN_SECONDS=5,
    VOICE_MAX_SESSION_MINUTES=20,
    VOICE_VAD_THRESHOLD=.55,
    VOICE_VAD_PREFIX_PADDING_MS=300,
    VOICE_VAD_SILENCE_DURATION_MS=700,
    VOICE_TEST_USERS=set(),
)
class VoiceSessionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("surgeon", "surgeon@example.com", "test-password-123")
        self.admin = User.objects.create_user("manager", "manager@example.com", "test-password-123")
        admin_group, _ = Group.objects.get_or_create(name="ADMIN")
        self.admin.groups.add(admin_group)

    def test_anonymous_user_must_log_in(self):
        response = self.client.post(reverse("qa:voice_session"))
        self.assertEqual(response.status_code, 302)

    @override_settings(VOICE_FEATURE_ENABLED=False)
    def test_disabled_feature_is_not_exposed(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("qa:voice_session")).status_code, 404)
        self.assertEqual(
            self.client.post(
                reverse("qa:voice_query"),
                data='{"question":"A question","call_id":"call-disabled"}',
                content_type="application/json",
            ).status_code,
            404,
        )

    def test_unapproved_user_is_forbidden(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse("qa:voice_session")).status_code, 403)

    @override_settings(VOICE_TEST_USERS={"surgeon"})
    def test_approved_user_can_open_voice_dashboard(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("qa:voice_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "qa/voice.html")
        self.assertContains(response, reverse("qa:voice_session"))
        self.assertContains(response, "Start conversation")

    def test_unapproved_user_cannot_open_voice_dashboard(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("qa:voice_dashboard")).status_code, 403)

    @override_settings(VOICE_TEST_USERS={"surgeon@example.com"})
    @patch("apps.qa.views.create_realtime_client_secret")
    def test_approved_email_can_create_session(self, create_secret):
        create_secret.return_value = {
            "client_secret": "ek_test",
            "expires_at": 123456,
            "model": "gpt-realtime",
            "voice": "marin",
            "max_session_minutes": 20,
        }
        self.client.force_login(self.user)

        response = self.client.post(reverse("qa:voice_session"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["client_secret"], "ek_test")
        self.assertEqual(response["Cache-Control"], "no-store")
        create_secret.assert_called_once_with(self.user)

    @patch("apps.qa.views.create_realtime_client_secret")
    def test_admin_group_can_create_session(self, create_secret):
        create_secret.return_value = {
            "client_secret": "ek_test",
            "expires_at": 123456,
            "model": "gpt-realtime",
            "voice": "marin",
            "max_session_minutes": 20,
        }
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("qa:voice_session")).status_code, 200)

    @patch("apps.qa.views.create_realtime_client_secret")
    def test_session_creation_is_throttled(self, create_secret):
        create_secret.return_value = {
            "client_secret": "ek_test",
            "expires_at": 123456,
            "model": "gpt-realtime",
            "voice": "marin",
            "max_session_minutes": 20,
        }
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("qa:voice_session")).status_code, 200)
        response = self.client.post(reverse("qa:voice_session"))
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "5")

    @patch("apps.qa.voice.OpenAI")
    def test_permanent_api_key_is_kept_server_side(self, openai_class):
        user = self.admin
        openai_class.return_value.realtime.client_secrets.create.return_value = SimpleNamespace(
            value="ek_short_lived", expires_at=123456
        )

        result = create_realtime_client_secret(user)

        self.assertEqual(result["client_secret"], "ek_short_lived")
        openai_class.assert_called_once_with(api_key="test-key", timeout=20.0, max_retries=1)
        call = openai_class.return_value.realtime.client_secrets.create.call_args
        self.assertEqual(call.kwargs["expires_after"]["seconds"], 60)
        self.assertEqual(call.kwargs["session"]["audio"]["output"]["voice"], "marin")
        self.assertEqual(
            call.kwargs["session"]["audio"]["input"]["transcription"]["model"],
            "gpt-transcribe",
        )
        self.assertEqual(
            call.kwargs["session"]["audio"]["input"]["turn_detection"],
            {
                "type": "server_vad",
                "threshold": .55,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 700,
                "create_response": True,
                "interrupt_response": True,
            },
        )
        self.assertEqual(
            call.kwargs["extra_headers"]["OpenAI-Safety-Identifier"], safety_identifier(user)
        )
        self.assertEqual(call.kwargs["session"]["tool_choice"], "required")
        self.assertEqual(call.kwargs["session"]["tools"][0]["name"], "search_vetdojo_knowledge")
        self.assertNotIn("test-key", result.values())

    @override_settings(VOICE_CLIENT_SECRET_TTL_SECONDS=5)
    def test_invalid_secret_ttl_is_rejected(self):
        with self.assertRaises(VoiceConfigurationError):
            create_realtime_client_secret(self.admin)

    @override_settings(VOICE_VAD_THRESHOLD=1.1)
    def test_invalid_vad_threshold_is_rejected(self):
        with self.assertRaises(VoiceConfigurationError):
            create_realtime_client_secret(self.admin)

    @patch("apps.qa.views.answer_question")
    def test_voice_query_is_saved_and_returns_references(self, answer_question_mock):
        self.client.force_login(self.admin)

        def create_answer(question):
            answer = Answer.objects.create(
                question=question,
                text="Use the dorsal approach [Source 1].",
                structured_content={"summary": "Use the dorsal approach [Source 1]."},
                source_type=Answer.SourceType.CLIENT,
            )
            AnswerSource.objects.create(
                answer=answer, chunk_id=42, source_type="journal_article",
                source_id="article-42", source_title="Surgical study",
                source_url="https://doi.org/10.1000/test", chunk_index=0,
                similarity=.9, excerpt="Supporting text", citation_order=1,
                citation_metadata={"pdf_page_start": 3},
            )
            return answer

        answer_question_mock.side_effect = create_answer
        response = self.client.post(
            reverse("qa:voice_query"),
            data='{"question": "  What approach is recommended?  ", "call_id": "call-42"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        question = Question.objects.get()
        self.assertEqual(question.user, self.admin)
        self.assertEqual(question.text, "What approach is recommended?")
        self.assertEqual(response.json()["sources"][0]["pdf_page"], 3)
        self.assertEqual(response.json()["spoken_answer"], "Use the dorsal approach.")

        duplicate = self.client.post(
            reverse("qa:voice_query"),
            data='{"question": "What approach is recommended?", "call_id": "call-42"}',
            content_type="application/json",
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.json()["question_id"], question.pk)
        self.assertEqual(Question.objects.count(), 1)
        self.assertEqual(answer_question_mock.call_count, 1)

    def test_voice_query_rejects_invalid_json(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("qa:voice_query"), data="not-json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_unapproved_user_cannot_submit_voice_query(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("qa:voice_query"),
            data='{"question":"A question","call_id":"call-denied"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_voice_query_requires_valid_call_identifier(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("qa:voice_query"),
            data='{"question":"A question","call_id":"bad call id"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    @patch("apps.qa.views.answer_question", side_effect=RuntimeError("provider failure"))
    def test_voice_query_failure_is_safe_and_question_is_retained(self, _answer_question):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("qa:voice_query"),
            data='{"question":"What should I do?","call_id":"call-failure"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("provider failure", response.content.decode())
        self.assertEqual(Question.objects.get().text, "What should I do?")
