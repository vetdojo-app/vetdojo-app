import tempfile
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.test import TestCase

from apps.qa.models import JournalArticleDetails, KnowledgeSourceProfile


class JournalPdfStorageTests(TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.field = JournalArticleDetails._meta.get_field("pdf_file")
        self.original_storage = self.field.storage
        self.field.storage = FileSystemStorage(location=self.temporary_directory.name)
        self.profile = KnowledgeSourceProfile.objects.create(source_document_id=987654)

    def tearDown(self):
        self.field.storage = self.original_storage
        self.temporary_directory.cleanup()

    def create_article(self):
        article = JournalArticleDetails(
            profile=self.profile,
            original_filename="article.pdf",
            article_title="Test journal article",
        )
        article.pdf_file.save("article.pdf", ContentFile(b"%PDF-test"), save=False)
        article.save()
        return article

    def test_deleting_article_removes_private_file_after_commit(self):
        article = self.create_article()
        stored_path = Path(article.pdf_file.path)
        self.assertTrue(stored_path.exists())

        with self.captureOnCommitCallbacks(execute=True):
            article.delete()

        self.assertFalse(stored_path.exists())

    def test_replacing_article_removes_previous_file_after_commit(self):
        article = self.create_article()
        previous_path = Path(article.pdf_file.path)
        article.pdf_file.save("replacement.pdf", ContentFile(b"%PDF-replacement"), save=False)
        replacement_path = Path(article.pdf_file.path)

        with self.captureOnCommitCallbacks(execute=True):
            article.save()

        self.assertFalse(previous_path.exists())
        self.assertTrue(replacement_path.exists())
