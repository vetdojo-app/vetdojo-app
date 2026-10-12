import hashlib
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from pypdf import PdfReader
from pypdf.errors import PdfReadError


ALLOWED_PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}


@dataclass(frozen=True)
class PdfInspection:
    sha256: str
    page_count: int
    extracted_text_chars: int


def _rewind(uploaded_file, position=0):
    try:
        uploaded_file.seek(position)
    except (AttributeError, OSError):
        pass


def _file_size(uploaded_file):
    size = getattr(uploaded_file, "size", None)
    if size is not None:
        return size
    original_position = uploaded_file.tell()
    uploaded_file.seek(0, 2)
    size = uploaded_file.tell()
    uploaded_file.seek(original_position)
    return size


def _sha256(uploaded_file):
    digest = hashlib.sha256()
    _rewind(uploaded_file)
    while True:
        block = uploaded_file.read(1024 * 1024)
        if not block:
            break
        digest.update(block)
    _rewind(uploaded_file)
    return digest.hexdigest()


def inspect_journal_pdf(uploaded_file):
    """Validate a journal PDF and return stable inspection metadata."""

    cached = getattr(uploaded_file, "_vetdojo_pdf_inspection", None)
    if cached:
        return cached

    original_position = 0
    try:
        original_position = uploaded_file.tell()
    except (AttributeError, OSError):
        pass

    try:
        filename = Path(getattr(uploaded_file, "name", "")).name
        if Path(filename).suffix.lower() != ".pdf":
            raise ValidationError("Upload a file with a .pdf extension.", code="invalid_extension")

        content_type = getattr(uploaded_file, "content_type", None)
        if content_type and content_type.lower() not in ALLOWED_PDF_CONTENT_TYPES:
            raise ValidationError("The uploaded file is not identified as a PDF.", code="invalid_content_type")

        size = _file_size(uploaded_file)
        if size <= 0:
            raise ValidationError("The uploaded PDF is empty.", code="empty_file")
        if size > settings.JOURNAL_PDF_MAX_BYTES:
            limit_mb = settings.JOURNAL_PDF_MAX_BYTES // (1024 * 1024)
            raise ValidationError(
                f"The PDF exceeds the {limit_mb} MB upload limit.",
                code="file_too_large",
            )

        _rewind(uploaded_file)
        if uploaded_file.read(5) != b"%PDF-":
            raise ValidationError("The file does not have a valid PDF signature.", code="invalid_signature")

        checksum = _sha256(uploaded_file)
        try:
            reader = PdfReader(uploaded_file, strict=False)
        except (PdfReadError, OSError, ValueError) as exc:
            raise ValidationError("The PDF is damaged or cannot be read.", code="unreadable_pdf") from exc

        if reader.is_encrypted:
            raise ValidationError("Encrypted or password-protected PDFs are not supported.", code="encrypted_pdf")

        page_count = len(reader.pages)
        if page_count == 0:
            raise ValidationError("The PDF does not contain any pages.", code="no_pages")
        if page_count > settings.JOURNAL_PDF_MAX_PAGES:
            raise ValidationError(
                f"The PDF exceeds the {settings.JOURNAL_PDF_MAX_PAGES}-page limit.",
                code="too_many_pages",
            )

        extracted_text_chars = 0
        try:
            for page in reader.pages:
                extracted_text_chars += len((page.extract_text() or "").strip())
        except Exception as exc:
            raise ValidationError("Text could not be extracted from this PDF.", code="extraction_failed") from exc

        if extracted_text_chars < settings.JOURNAL_PDF_MIN_TEXT_CHARS:
            raise ValidationError(
                "This PDF does not contain enough extractable text. Scanned PDFs require OCR and are not supported yet.",
                code="insufficient_text",
            )

        inspection = PdfInspection(
            sha256=checksum,
            page_count=page_count,
            extracted_text_chars=extracted_text_chars,
        )
        uploaded_file._vetdojo_pdf_inspection = inspection
        return inspection
    finally:
        _rewind(uploaded_file, original_position)


def validate_journal_pdf(uploaded_file):
    inspect_journal_pdf(uploaded_file)
