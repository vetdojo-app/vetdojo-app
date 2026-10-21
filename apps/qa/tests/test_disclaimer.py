from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.qa.disclaimer import DISCLAIMER_CONTENT_HASH, DISCLAIMER_VERSION
from apps.qa.models import AIDisclaimerAcceptance


class AIDisclaimerAcceptanceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="disclaimer-user",
            email="disclaimer-user@example.com",
            password="test-password",
        )
        self.other_user = user_model.objects.create_user(
            username="other-disclaimer-user",
            email="other-disclaimer-user@example.com",
            password="test-password",
        )

    def create_acceptance(self, **overrides):
        values = {
            "user": self.user,
            "disclaimer_version": "2026-10-01",
            "content_hash": "a" * 64,
            "source": AIDisclaimerAcceptance.Source.TEXT,
        }
        values.update(overrides)
        return AIDisclaimerAcceptance.objects.create(**values)

    def test_records_versioned_acceptance_with_source_and_fingerprint(self):
        acceptance = self.create_acceptance()

        self.assertEqual(acceptance.user, self.user)
        self.assertEqual(acceptance.disclaimer_version, "2026-10-01")
        self.assertEqual(acceptance.content_hash, "a" * 64)
        self.assertEqual(acceptance.source, AIDisclaimerAcceptance.Source.TEXT)
        self.assertIsNotNone(acceptance.accepted_at)

    def test_user_cannot_accept_same_version_twice(self):
        self.create_acceptance()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_acceptance(source=AIDisclaimerAcceptance.Source.VOICE)

    def test_user_can_accept_a_new_disclaimer_version(self):
        self.create_acceptance()
        self.create_acceptance(
            disclaimer_version="2026-11-01",
            content_hash="b" * 64,
            source=AIDisclaimerAcceptance.Source.VOICE,
        )

        self.assertEqual(self.user.ai_disclaimer_acceptances.count(), 2)

    def test_different_users_can_accept_the_same_version(self):
        self.create_acceptance()
        self.create_acceptance(user=self.other_user)

        self.assertEqual(AIDisclaimerAcceptance.objects.count(), 2)

    def test_acceptances_are_deleted_with_the_user(self):
        self.create_acceptance()

        self.user.delete()

        self.assertFalse(AIDisclaimerAcceptance.objects.exists())


class AIDisclaimerFlowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="flow-user",
            email="flow-user@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)

    def accept_current_disclaimer(self, source=AIDisclaimerAcceptance.Source.TEXT):
        return AIDisclaimerAcceptance.objects.create(
            user=self.user,
            disclaimer_version=DISCLAIMER_VERSION,
            content_hash=DISCLAIMER_CONTENT_HASH,
            source=source,
        )

    def test_first_text_assistant_visit_redirects_to_disclaimer(self):
        response = self.client.get(reverse("qa:dashboard"))

        self.assertRedirects(
            response,
            f'{reverse("qa:disclaimer")}?source=text&next=%2Fapp%2F',
            fetch_redirect_response=False,
        )

    def test_acceptance_records_exact_version_hash_and_returns_to_assistant(self):
        response = self.client.post(
            reverse("qa:disclaimer"),
            {
                "acknowledgement": "accepted",
                "source": AIDisclaimerAcceptance.Source.VOICE,
                "next": reverse("qa:voice_dashboard"),
            },
        )

        self.assertRedirects(response, reverse("qa:voice_dashboard"), fetch_redirect_response=False)
        acceptance = AIDisclaimerAcceptance.objects.get(user=self.user)
        self.assertEqual(acceptance.disclaimer_version, DISCLAIMER_VERSION)
        self.assertEqual(acceptance.content_hash, DISCLAIMER_CONTENT_HASH)
        self.assertEqual(acceptance.source, AIDisclaimerAcceptance.Source.VOICE)

    def test_acknowledgement_is_required(self):
        response = self.client.post(
            reverse("qa:disclaimer"),
            {"source": "text", "next": reverse("qa:dashboard")},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please confirm the acknowledgement")
        self.assertFalse(AIDisclaimerAcceptance.objects.exists())

    def test_external_next_url_is_rejected(self):
        response = self.client.post(
            reverse("qa:disclaimer"),
            {
                "acknowledgement": "accepted",
                "source": "text",
                "next": "https://example.net/unsafe",
            },
        )

        self.assertRedirects(response, reverse("qa:dashboard"), fetch_redirect_response=False)

    def test_accepted_user_sees_introduction_and_short_notice(self):
        self.accept_current_disclaimer()

        response = self.client.get(reverse("qa:dashboard"))

        self.assertContains(response, "Welcome to the VetDojo AI Surgical Assistant")
        self.assertContains(response, "AI-generated educational information")
        self.assertContains(response, "do not enter identifying or confidential information")

    def test_voice_api_requires_acceptance(self):
        response = self.client.post(reverse("qa:voice_session"))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.json()["disclaimer_required"])
