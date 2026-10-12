from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from apps.qa.pdf_validation import inspect_journal_pdf


def uploaded_pdf(content=b"%PDF-1.7 test content", **kwargs):
    return SimpleUploadedFile(
        kwargs.pop("name", "article.pdf"),
        content,
        content_type=kwargs.pop("content_type", "application/pdf"),
        **kwargs,
    )


def reader_with_text(text="A" * 300, encrypted=False):
    page = Mock()
    page.extract_text.return_value = text
    reader = Mock()
    reader.is_encrypted = encrypted
    reader.pages = [page]
    return reader


class JournalPdfValidationTests(SimpleTestCase):
    @patch("apps.qa.pdf_validation.PdfReader")
    def test_valid_pdf_returns_checksum_page_count_and_text_count(self, pdf_reader):
        pdf_reader.return_value = reader_with_text()
        result = inspect_journal_pdf(uploaded_pdf())
        self.assertEqual(result.page_count, 1)
        self.assertEqual(result.extracted_text_chars, 300)
        self.assertEqual(len(result.sha256), 64)

    def test_rejects_invalid_signature(self):
        with self.assertRaisesMessage(ValidationError, "valid PDF signature"):
            inspect_journal_pdf(uploaded_pdf(content=b"not a real pdf"))

    def test_rejects_incorrect_content_type(self):
        with self.assertRaisesMessage(ValidationError, "not identified as a PDF"):
            inspect_journal_pdf(uploaded_pdf(content_type="text/plain"))

    @override_settings(JOURNAL_PDF_MAX_BYTES=10)
    def test_rejects_file_over_configured_size_limit(self):
        with self.assertRaisesMessage(ValidationError, "upload limit"):
            inspect_journal_pdf(uploaded_pdf(content=b"%PDF-" + b"x" * 20))

    @patch("apps.qa.pdf_validation.PdfReader")
    def test_rejects_encrypted_pdf(self, pdf_reader):
        pdf_reader.return_value = reader_with_text(encrypted=True)
        with self.assertRaisesMessage(ValidationError, "password-protected"):
            inspect_journal_pdf(uploaded_pdf())

    @patch("apps.qa.pdf_validation.PdfReader")
    def test_rejects_image_only_pdf(self, pdf_reader):
        pdf_reader.return_value = reader_with_text(text="")
        with self.assertRaisesMessage(ValidationError, "does not contain enough extractable text"):
            inspect_journal_pdf(uploaded_pdf())

    @patch("apps.qa.pdf_validation.PdfReader")
    def test_inspection_restores_file_position(self, pdf_reader):
        pdf_reader.return_value = reader_with_text()
        document = uploaded_pdf()
        document.seek(3)
        inspect_journal_pdf(document)
        self.assertEqual(document.tell(), 3)
