import io
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from .pdf_extraction import (
    ExtractedPage,
    analyze_article_metadata,
    normalize_pdf_text,
    pages_as_source_text,
    repeated_edge_lines,
)


class PdfExtractionUtilityTests(SimpleTestCase):
    def test_normalizes_ligatures_and_line_break_hyphenation(self):
        self.assertEqual(normalize_pdf_text("surgi\ufb01cal tech-\nnique"), "surgifical technique")

    def test_detects_repeated_page_edge_lines(self):
        pages = [
            "Veterinary Surgery Journal\nFirst page text\n1",
            "Veterinary Surgery Journal\nSecond page text\n2",
            "Veterinary Surgery Journal\nThird page text\n3",
        ]
        self.assertIn("Veterinary Surgery Journal", repeated_edge_lines(pages))

    def test_builds_page_aware_source_text(self):
        page = ExtractedPage(2, "604", "raw", "Useful evidence", ())
        self.assertEqual(
            pages_as_source_text([page]),
            "[PDF page 2; printed page 604]\nUseful evidence",
        )

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_PDF_MODEL="gpt-test")
    @patch("apps.management.pdf_extraction.OpenAI")
    def test_openai_metadata_analysis_uses_structured_pdf_input(self, openai_class):
        payload = '{"article_title":"Study","authors":"A","journal":"J","publication_year":2014,"volume":"1","issue":"2","article_pages":"3-4","doi":"10.1/x","abstract":"A","quality_warnings":[]}'
        client = openai_class.return_value
        client.responses.create.return_value = SimpleNamespace(output_text=payload)
        file_field = MagicMock()
        file_field.open.return_value.__enter__.return_value = io.BytesIO(b"%PDF-test")
        article = SimpleNamespace(pdf_file=file_field, original_filename="study.pdf")

        result = analyze_article_metadata(article)

        self.assertEqual(result["article_title"], "Study")
        call = client.responses.create.call_args.kwargs
        self.assertEqual(call["model"], "gpt-test")
        self.assertEqual(call["input"][0]["content"][0]["type"], "input_file")
        self.assertEqual(call["text"]["format"]["type"], "json_schema")
