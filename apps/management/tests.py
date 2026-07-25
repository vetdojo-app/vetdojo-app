from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from types import SimpleNamespace
from unittest.mock import patch

from apps.accounts.models import User
from apps.qa.models import KnowledgeProcessingRun, KnowledgeSourceProfile


class ManagementAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("member", "member@example.com", "test-password-123")
        self.staff = User.objects.create_user("manager", "manager@example.com", "test-password-123", is_staff=True)
        self.admin = User.objects.create_user("admin-manager", "admin-manager@example.com", "test-password-123")
        self.admin.groups.add(Group.objects.get(name="ADMIN"))

    def test_anonymous_user_is_sent_to_staff_login(self):
        response = self.client.get(reverse("management:dashboard"))
        self.assertRedirects(response, f"{reverse('management:login')}?next={reverse('management:dashboard')}")

    def test_non_staff_user_cannot_access_management(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("management:dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_ordinary_staff_cannot_open_management(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("management:dashboard")).status_code, 302)

    def test_admin_group_can_open_dashboard_without_local_rag_tables(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("management:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Knowledge dashboard")

    @patch("apps.management.views.process_knowledge_source.delay")
    @patch("apps.management.views.RagSourceDocument.objects.get")
    def test_processing_action_creates_pending_audit_run(self, source_get, delay):
        source_get.return_value = SimpleNamespace(content="Clinical source text")
        profile = KnowledgeSourceProfile.objects.create(source_document_id=42, created_by=self.admin, updated_by=self.admin)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("management:process", args=[42]))
        self.assertRedirects(response, reverse("management:source_list"))
        profile.refresh_from_db()
        self.assertEqual(profile.status, KnowledgeSourceProfile.Status.PENDING)
        run = KnowledgeProcessingRun.objects.get()
        self.assertEqual(run.status, KnowledgeProcessingRun.Status.PENDING)
        delay.assert_called_once_with(run.pk)

    def test_public_account_cannot_sign_in_on_management_login(self):
        response = self.client.post(reverse("management:login"), {"username": "member", "password": "test-password-123"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "does not have management access")

    def test_management_password_change_stays_in_management_portal(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("management:password_change"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Change password")
