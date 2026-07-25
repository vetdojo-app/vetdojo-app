from django.test import SimpleTestCase

from .tasks import normalize_source_text, split_chunks


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
