import json

from django.test import TestCase, override_settings

from apps.qa.services import (
    RetrievedChunk, cited_chunks, clean_model_text, evidence_is_sufficient, extract_keywords,
    normalize_metadata, parse_model_json, rank_candidates, select_context,
)


def chunk(index, similarity=.8, source="video-1", text="Clinical transcript content", title="Video", relevance=0):
    return RetrievedChunk(
        index, "youtube_video", source, title, "https://youtube.com/watch?v=x",
        index, text, similarity, {}, relevance_score=relevance,
    )


class RetrievalSelectionTests(TestCase):
    def test_clinical_keywords_are_normalized(self):
        keywords = extract_keywords("What are the key considerations when repairing tibial fractures in young dogs?")
        self.assertIn("repair", keywords)
        self.assertIn("tibial", keywords)
        self.assertIn("fracture", keywords)
        self.assertNotIn("considerations", keywords)

    def test_relevant_title_ranks_above_unrelated_vector_match(self):
        unrelated = chunk(1, .72, title="Toe amputation and lymph node removal", text="Bandaging and wound care")
        relevant = chunk(2, .55, title="Tibial fracture repair in a dog", text="Plate fixation in a puppy")
        ranked = rank_candidates([unrelated, relevant], ["tibial", "fracture", "repair", "dog"])
        self.assertEqual(ranked[0].chunk_id, relevant.chunk_id)
        self.assertGreater(ranked[0].lexical_score, ranked[1].lexical_score)

    @override_settings(RAG_CONTEXT_COUNT=5, RAG_MAX_CONTEXT_CHARS=1000, RAG_MAX_CHUNKS_PER_SOURCE=3)
    def test_neighboring_chunks_can_be_kept_for_procedural_context(self):
        selected = select_context([chunk(1), chunk(2), chunk(3), chunk(4)])
        self.assertEqual([item.chunk_index for item in selected], [1, 2, 3])

    @override_settings(RAG_MIN_RELEVANCE=.5)
    def test_sufficiency_uses_combined_relevance(self):
        self.assertTrue(evidence_is_sufficient([chunk(1, relevance=.7)]))
        self.assertFalse(evidence_is_sufficient([chunk(1, relevance=.4)]))
        self.assertFalse(evidence_is_sufficient([]))

    def test_grounded_response_requires_valid_citations(self):
        chunks = [chunk(1, source="a"), chunk(2, source="a"), chunk(3, source="b")]
        content = {"evidence_sufficient": True, "cited_sources": [1, 2, 3]}
        self.assertEqual([item.source_id for item in cited_chunks(content, chunks)], ["a", "b"])
        self.assertEqual(cited_chunks({"evidence_sufficient": False, "cited_sources": [1]}, chunks), [])
        self.assertEqual(cited_chunks({"evidence_sufficient": True, "cited_sources": []}, chunks), [])

    def test_model_json_carries_evidence_and_citations(self):
        parsed = parse_model_json('{"evidence_sufficient": true, "summary": "Answer", "sections": [], "limitations": [], "cited_sources": [1, "2", 1, 0]}')
        self.assertTrue(parsed["evidence_sufficient"])
        self.assertEqual(parsed["cited_sources"], [1, 2])

    def test_string_limitation_is_normalized_to_one_list_item(self):
        parsed = parse_model_json('{"evidence_sufficient": true, "summary": "Answer", "sections": [], "limitations": "General principles only.", "cited_sources": []}')
        self.assertEqual(parsed["limitations"], ["General principles only."])

    def test_legacy_character_list_is_reassembled(self):
        raw = json.dumps({"evidence_sufficient": True, "summary": "Answer", "sections": [], "limitations": list("General principles only."), "cited_sources": []})
        self.assertEqual(parse_model_json(raw)["limitations"], ["General principles only."])

    def test_invalid_json_is_not_treated_as_grounded(self):
        parsed = parse_model_json("Plain answer")
        self.assertFalse(parsed["evidence_sufficient"])
        self.assertEqual(parsed["summary"], "Plain answer")

    def test_metadata_is_normalized_from_json_string(self):
        self.assertEqual(normalize_metadata('{"language": "en"}'), {"language": "en"})
        self.assertEqual(normalize_metadata("not-json"), {})
        self.assertEqual(normalize_metadata({"language": "en"}), {"language": "en"})

    def test_markdown_emphasis_is_removed_from_model_text(self):
        self.assertEqual(clean_model_text("1. **Renal Disease:** Details"), "1. Renal Disease: Details")
        parsed = parse_model_json('{"evidence_sufficient": true, "summary": "**Summary**", "sections": [{"heading": "**Heading**", "content": "**Bold:** text"}], "limitations": ["**Limit:** note"], "cited_sources": []}')
        self.assertEqual(parsed["summary"], "Summary")
        self.assertEqual(parsed["sections"][0]["content"], "Bold: text")
        self.assertEqual(parsed["limitations"], ["Limit: note"])
