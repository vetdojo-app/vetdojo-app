import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TransactionTestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.qa.models import (
    JournalArticleDetails,
    JournalArticlePage,
    KnowledgeProcessingRun,
    KnowledgeSourceProfile,
    RagSourceDocument,
)
from apps.qa.pdf_validation import PdfInspection
from apps.qa.storage import PrivateMediaStorage

from .forms import JournalArticleForm
from .tasks import extract_journal_article, process_knowledge_source


def text_pdf_bytes(text="Veterinary journal article evidence. " * 20):
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 10 Tf 50 750 Td ({escaped}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(content))
        content.extend(f"{number} 0 obj\n".encode("ascii"))
        content.extend(obj)
        content.extend(b"\nendobj\n")
    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(content)


class JournalArticleManagementTests(TransactionTestCase):
    reset_sequences = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.created_source_table = RagSourceDocument._meta.db_table not in connection.introspection.table_names()
        if cls.created_source_table:
            with connection.schema_editor() as schema_editor:
                schema_editor.create_model(RagSourceDocument)

    @classmethod
    def tearDownClass(cls):
        if cls.created_source_table:
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(RagSourceDocument)
        super().tearDownClass()

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.pdf_field = JournalArticleDetails._meta.get_field("pdf_file")
        self.original_storage = self.pdf_field.storage
        self.pdf_field.storage = FileSystemStorage(location=self.temporary_directory.name)
        self.admin = User.objects.create_user(
            "article-admin", "article-admin@example.com", "test-password-123"
        )
        admin_group, _ = Group.objects.get_or_create(name="ADMIN")
        self.admin.groups.add(admin_group)
        self.member = User.objects.create_user(
            "article-member", "article-member@example.com", "test-password-123"
        )

    def tearDown(self):
        self.pdf_field.storage = self.original_storage
        self.temporary_directory.cleanup()

    def article_data(self, **overrides):
        data = {
            "article_title": "Laryngeal surgery study",
            "authors": "Example A, Example B",
            "journal": "Veterinary Surgery",
            "publication_year": "2014",
            "volume": "43",
            "issue": "6",
            "article_pages": "602-606",
            "doi": "https://doi.org/10.1000/example",
            "publisher_url": "https://example.com/article",
            "abstract": "A reviewed veterinary journal article.",
            "metadata_reviewed": "on",
            "category": "",
            "notes": "",
        }
        data.update(overrides)
        return data

    def pdf_upload(self, name="article.pdf", content=None):
        content = content if content is not None else text_pdf_bytes()
        return SimpleUploadedFile(name, content, content_type="application/pdf")

    def create_article(self, checksum="a" * 64):
        self.client.force_login(self.admin)
        inspection = PdfInspection(sha256=checksum, page_count=6, extracted_text_chars=12000)
        with patch("apps.management.forms.inspect_journal_pdf", return_value=inspection):
            response = self.client.post(
                reverse("management:article_create"),
                {**self.article_data(), "pdf_file": self.pdf_upload()},
            )
        self.assertEqual(response.status_code, 302)
        return JournalArticleDetails.objects.get()

    def test_anonymous_user_cannot_open_article_upload(self):
        response = self.client.get(reverse("management:article_create"))
        self.assertRedirects(
            response,
            f"{reverse('management:login')}?next={reverse('management:article_create')}",
        )

    def test_admin_can_open_article_upload(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("management:article_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add journal article")
        self.assertContains(response, 'enctype="multipart/form-data"')

    def test_private_pdf_edit_form_does_not_request_a_public_storage_url(self):
        article = self.create_article()
        private_file = article.pdf_file
        private_file.storage = PrivateMediaStorage()
        article.pdf_file = private_file

        rendered = JournalArticleForm(instance=article).as_p()

        self.assertIn('type="file"', rendered)
        self.assertNotIn("Currently", rendered)

    def test_admin_can_create_inactive_journal_article(self):
        article = self.create_article()
        profile = article.profile
        source = RagSourceDocument.objects.get(pk=profile.source_document_id)

        self.assertEqual(source.source_type, "journal_article")
        self.assertEqual(source.source_title, article.article_title)
        self.assertEqual(source.extraction_status, "pending")
        self.assertEqual(profile.status, KnowledgeSourceProfile.Status.DRAFT)
        self.assertFalse(profile.is_active)
        self.assertEqual(article.file_sha256, "a" * 64)
        self.assertEqual(article.pdf_page_count, 6)
        self.assertEqual(article.doi, "10.1000/example")
        self.assertTrue(Path(article.pdf_file.path).exists())

    def test_duplicate_pdf_is_rejected(self):
        self.create_article()
        inspection = PdfInspection(sha256="a" * 64, page_count=6, extracted_text_chars=12000)
        with patch("apps.management.forms.inspect_journal_pdf", return_value=inspection):
            response = self.client.post(
                reverse("management:article_create"),
                {
                    **self.article_data(article_title="Duplicate article"),
                    "pdf_file": self.pdf_upload(name="duplicate.pdf"),
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already been uploaded")
        self.assertEqual(JournalArticleDetails.objects.count(), 1)

    def test_replacing_processed_article_deactivates_it_for_reprocessing(self):
        article = self.create_article()
        profile = article.profile
        profile.status = KnowledgeSourceProfile.Status.COMPLETED
        profile.is_active = True
        profile.approved_by = self.admin
        profile.save()
        source_id = profile.source_document_id

        inspection = PdfInspection(sha256="b" * 64, page_count=7, extracted_text_chars=14000)
        with patch("apps.management.forms.inspect_journal_pdf", return_value=inspection):
            response = self.client.post(
                reverse("management:article_edit", args=[source_id]),
                {
                    **self.article_data(),
                    "pdf_file": self.pdf_upload(name="replacement.pdf", content=text_pdf_bytes("Replacement veterinary evidence. " * 20)),
                },
            )
        self.assertRedirects(response, reverse("management:article_edit", args=[source_id]))
        article.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(article.file_sha256, "b" * 64)
        self.assertEqual(article.pdf_page_count, 7)
        self.assertEqual(profile.status, KnowledgeSourceProfile.Status.NEEDS_REPROCESSING)
        self.assertFalse(profile.is_active)
        self.assertIsNone(profile.approved_by)

    def test_pdf_download_requires_management_access(self):
        article = self.create_article()
        url = reverse("management:article_download", args=[article.profile.source_document_id])

        self.client.force_login(self.member)
        denied = self.client.get(url)
        self.assertEqual(denied.status_code, 302)

        self.client.force_login(self.admin)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response["Cache-Control"], "private, no-store")
        b"".join(response.streaming_content)

    @patch("apps.management.views.extract_journal_article.delay")
    def test_admin_can_queue_article_extraction(self, delayed_task):
        article = self.create_article()
        source = RagSourceDocument.objects.get(pk=article.profile.source_document_id)

        response = self.client.post(
            reverse("management:article_extract", args=[source.pk]),
            {"use_openai": "0"},
        )

        self.assertRedirects(response, reverse("management:article_edit", args=[source.pk]))
        source.refresh_from_db()
        article.refresh_from_db()
        self.assertEqual(source.extraction_status, "queued")
        self.assertEqual(
            article.metadata_analysis_status,
            JournalArticleDetails.MetadataAnalysisStatus.NOT_REQUESTED,
        )
        delayed_task.assert_called_once_with(article.pk, use_openai=False)

    def test_local_extraction_saves_page_aware_text(self):
        article = self.create_article()
        source = RagSourceDocument.objects.get(pk=article.profile.source_document_id)

        result = extract_journal_article(article.pk, use_openai=False)

        article.refresh_from_db()
        source.refresh_from_db()
        page = JournalArticlePage.objects.get(article=article)
        self.assertEqual(result["pages"], 1)
        self.assertEqual(source.extraction_status, "success")
        self.assertIn("[PDF page 1]", source.content)
        self.assertIn("Veterinary journal article evidence", page.cleaned_text)
        self.assertEqual(article.extraction_method, JournalArticleDetails.ExtractionMethod.LOCAL)
        self.assertIsNotNone(article.extracted_at)

    def test_replacing_pdf_removes_previous_extracted_pages_and_text(self):
        article = self.create_article()
        source = RagSourceDocument.objects.get(pk=article.profile.source_document_id)
        extract_journal_article(article.pk, use_openai=False)
        self.assertTrue(article.pages.exists())

        inspection = PdfInspection(sha256="c" * 64, page_count=1, extracted_text_chars=900)
        with patch("apps.management.forms.inspect_journal_pdf", return_value=inspection):
            response = self.client.post(
                reverse("management:article_edit", args=[source.pk]),
                {
                    **self.article_data(),
                    "pdf_file": self.pdf_upload("new.pdf", text_pdf_bytes("New evidence. " * 20)),
                },
            )

        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        article.refresh_from_db()
        self.assertEqual(source.content, "")
        self.assertEqual(source.extraction_status, "pending")
        self.assertFalse(article.pages.exists())
        self.assertIsNone(article.extracted_at)

    @patch("apps.management.views.process_knowledge_source.delay")
    def test_extracted_reviewed_article_can_queue_chunk_generation(self, delayed_task):
        article = self.create_article()
        source = RagSourceDocument.objects.get(pk=article.profile.source_document_id)
        extract_journal_article(article.pk, use_openai=False)

        response = self.client.post(reverse("management:process", args=[source.pk]))

        self.assertRedirects(response, reverse("management:source_list"))
        run = article.profile.processing_runs.get()
        self.assertEqual(run.status, KnowledgeProcessingRun.Status.PENDING)
        delayed_task.assert_called_once_with(run.pk)

    @patch("apps.management.views.process_knowledge_source.delay")
    def test_unextracted_article_cannot_queue_chunk_generation(self, delayed_task):
        article = self.create_article()
        source_id = article.profile.source_document_id

        response = self.client.post(reverse("management:process", args=[source_id]))

        self.assertRedirects(response, reverse("management:article_edit", args=[source_id]))
        self.assertFalse(article.profile.processing_runs.exists())
        delayed_task.assert_not_called()

    @patch("apps.management.views.extract_journal_article.delay")
    def test_save_and_analyze_accepts_pdf_with_only_optional_metadata(self, delayed_task):
        self.client.force_login(self.admin)
        inspection = PdfInspection(sha256="d" * 64, page_count=3, extracted_text_chars=6000)
        with patch("apps.management.forms.inspect_journal_pdf", return_value=inspection):
            response = self.client.post(
                reverse("management:article_create"),
                {
                    "pdf_file": self.pdf_upload(name="laryngeal-study.pdf"),
                    "article_title": "",
                    "category": "",
                    "notes": "",
                    "action": "analyze",
                },
            )

        article = JournalArticleDetails.objects.get()
        source = RagSourceDocument.objects.get(pk=article.profile.source_document_id)
        self.assertRedirects(response, reverse("management:article_edit", args=[source.pk]))
        self.assertEqual(article.article_title, "laryngeal study")
        self.assertEqual(source.extraction_status, "queued")
        delayed_task.assert_called_once_with(article.pk, use_openai=True)

    @patch("apps.management.views.process_knowledge_source.delay")
    def test_approve_and_publish_creates_publish_on_success_run(self, delayed_task):
        article = self.create_article()
        source = RagSourceDocument.objects.get(pk=article.profile.source_document_id)
        extract_journal_article(article.pk, use_openai=False)

        response = self.client.post(
            reverse("management:article_edit", args=[source.pk]),
            {**self.article_data(), "action": "publish"},
        )

        run = KnowledgeProcessingRun.objects.get()
        self.assertRedirects(response, reverse("management:article_edit", args=[source.pk]))
        self.assertTrue(run.publish_on_success)
        self.assertFalse(article.profile.is_active)
        delayed_task.assert_called_once_with(run.pk)

    def test_publish_on_success_activates_article_after_chunks_complete(self):
        article = self.create_article()
        source = RagSourceDocument.objects.get(pk=article.profile.source_document_id)
        extract_journal_article(article.pk, use_openai=False)
        run = KnowledgeProcessingRun.objects.create(
            profile=article.profile,
            action=KnowledgeProcessingRun.Action.INITIAL,
            embedding_model="text-embedding-3-small",
            embedding_dimensions=1536,
            chunk_size=900,
            chunk_overlap=120,
            initiated_by=self.admin,
            publish_on_success=True,
        )

        with patch("apps.management.tasks._embed_chunks", return_value=[[0.0] * 1536]), patch(
            "apps.management.tasks._replace_chunks"
        ):
            process_knowledge_source(run.pk)

        article.profile.refresh_from_db()
        self.assertTrue(article.profile.is_active)
        self.assertEqual(article.profile.status, KnowledgeSourceProfile.Status.COMPLETED)
        self.assertEqual(article.profile.approved_by, self.admin)

    def test_delete_confirmation_requires_only_second_post(self):
        article = self.create_article()
        source_id = article.profile.source_document_id

        confirmation = self.client.get(reverse("management:source_delete", args=[source_id]))
        self.assertContains(confirmation, "Yes, delete permanently")
        self.assertNotContains(confirmation, "Type <strong>")

        response = self.client.post(reverse("management:source_delete", args=[source_id]))
        self.assertRedirects(response, reverse("management:source_list"))
        self.assertFalse(JournalArticleDetails.objects.filter(pk=article.pk).exists())
        self.assertFalse(RagSourceDocument.objects.filter(pk=source_id).exists())
