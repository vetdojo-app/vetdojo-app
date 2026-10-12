from types import SimpleNamespace

from django.test import SimpleTestCase

from .tasks import normalize_source_text, split_article_pages, split_chunks


class KnowledgeProcessingHelpersTests(SimpleTestCase):
    def test_mojibake_is_repaired(self):
        self.assertEqual(normalize_source_text("Ownerâ€™s report"), "Owner’s report")

    def test_chunks_overlap_and_preserve_content(self):
        chunks = split_chunks("one two three four five six seven eight", chunk_size=4, overlap=1)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual(chunks[0][0], 0)
        self.assertGreater(chunks[0][2], 0)

    def test_overlap_must_be_smaller_than_chunk(self):
        with self.assertRaises(ValueError):
            split_chunks("text", chunk_size=4, overlap=4)

    def test_article_chunks_do_not_cross_pages_and_include_citation_metadata(self):
        article = SimpleNamespace(
            article_title="Laryngeal study", authors="A. Author", journal="Vet Journal",
            publication_year=2014, volume="43", issue="6", article_pages="602-606",
            doi="10.1000/example",
        )
        pages = [
            SimpleNamespace(pdf_page_number=1, printed_page_number="602", cleaned_text="first page evidence"),
            SimpleNamespace(pdf_page_number=2, printed_page_number="603", cleaned_text="second page evidence"),
        ]

        chunks = split_article_pages(pages, chunk_size=50, overlap=5, article=article)

        self.assertEqual(len(chunks), 2)
        self.assertEqual([chunk[0] for chunk in chunks], [0, 1])
        self.assertEqual(chunks[0][3]["pdf_page_start"], 1)
        self.assertEqual(chunks[0][3]["printed_page_start"], "602")
        self.assertEqual(chunks[1][3]["pdf_page_start"], 2)
        self.assertEqual(chunks[1][3]["doi"], "10.1000/example")
