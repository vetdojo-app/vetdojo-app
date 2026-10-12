import json
import time
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.qa.models import Answer, Question
from apps.qa.services import (
    ANSWER_RESPONSE_FORMAT, RetrievedChunk, cited_chunks, clean_model_text,
    enabled_source_types, evidence_is_sufficient, extract_keywords, generate_content,
    normalize_metadata, parse_model_json, rank_candidates, retrieve_chunks,
    save_answer, select_context, source_context_block,
)


def chunk(index, similarity=.8, source="video-1", text="Clinical transcript content", title="Video", relevance=0, source_type="youtube_video", metadata=None, url="https://youtube.com/watch?v=x"):
    return RetrievedChunk(
        index, source_type, source, title, url,
        index, text, similarity, metadata or {}, relevance_score=relevance,
    )


class RetrievalSelectionTests(TestCase):
    def test_enabled_sources_include_journal_articles_when_configured(self):
        config = SimpleNamespace(
            youtube_enabled=True,
            consultations_enabled=True,
            journal_articles_enabled=True,
        )
        self.assertEqual(
            enabled_source_types(config),
            ["youtube_video", "youtube_transcript", "consultation", "journal_article"],
        )

    def test_journal_articles_can_be_disabled_independently(self):
        config = SimpleNamespace(
            youtube_enabled=True,
            consultations_enabled=True,
            journal_articles_enabled=False,
        )
        self.assertNotIn("journal_article", enabled_source_types(config))

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
        selected = cited_chunks(content, chunks)
        self.assertEqual([item.source_id for item in selected], ["a", "a", "b"])
        self.assertEqual([item.prompt_index for item in selected], [1, 2, 3])
        self.assertEqual(cited_chunks({"evidence_sufficient": False, "cited_sources": [1]}, chunks), [])
        self.assertEqual(cited_chunks({"evidence_sufficient": True, "cited_sources": []}, chunks), [])
        self.assertEqual(cited_chunks({"evidence_sufficient": True, "cited_sources": [0, -1, 4]}, chunks), [])

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

    def test_journal_context_exposes_stable_source_and_page_metadata(self):
        article = chunk(
            9,
            source_type="journal_article",
            title="Laryngeal study",
            url="https://doi.org/10.1000/example",
            metadata={
                "authors": "A. Author", "journal": "Veterinary Surgery",
                "publication_year": 2014, "doi": "10.1000/example",
                "pdf_page_start": 3, "printed_page_start": "604",
            },
        )
        context = source_context_block(article, 2)
        self.assertIn('<SOURCE id="source2">', context)
        self.assertIn("Source type: journal_article", context)
        self.assertIn("Authors: A. Author", context)
        self.assertIn("PDF page: 3", context)
        self.assertIn("Printed page: 604", context)

    @patch("apps.qa.services.openai_client")
    @patch("apps.qa.services.knowledge_config")
    def test_generation_uses_structured_output_and_source_aware_prompt(self, config_mock, client_mock):
        config_mock.return_value = SimpleNamespace(chat_model="gpt-test")
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"evidence_sufficient":true,"summary":"Supported [Source 1]","sections":[],"limitations":[],"cited_sources":[1]}'))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        client_mock.return_value.chat.completions.create.return_value = response
        article = chunk(1, source_type="journal_article", metadata={"pdf_page_start": 4})

        content, _, _ = generate_content("Question", [article])

        call = client_mock.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(call["response_format"], ANSWER_RESPONSE_FORMAT)
        self.assertIn("journal articles", call["messages"][0]["content"])
        self.assertIn("PDF page: 4", call["messages"][1]["content"])
        self.assertEqual(content["cited_sources"], [1])

    @patch("apps.qa.services.knowledge_config")
    def test_saved_article_citation_preserves_prompt_number_and_metadata(self, config_mock):
        config_mock.return_value = SimpleNamespace(chat_model="gpt-test")
        user = User.objects.create_user("citation-user", "citation@example.com", "password")
        question = Question.objects.create(user=user, text="Question")
        article = chunk(
            7,
            source_type="journal_article",
            source="article-1",
            metadata={"source_document_id": 55, "pdf_page_start": 3, "doi": "10.1000/example"},
        )
        cited = cited_chunks({"evidence_sufficient": True, "cited_sources": [1]}, [article])

        answer = save_answer(
            question,
            {"summary": "Supported [Source 1]", "sections": [], "limitations": [], "cited_sources": [1]},
            Answer.SourceType.CLIENT,
            cited,
            time.monotonic(),
        )

        saved = answer.sources.get()
        self.assertEqual(saved.citation_order, 1)
        self.assertEqual(saved.source_document_id, 55)
        self.assertEqual(saved.citation_metadata["pdf_page_start"], 3)
        self.assertEqual(saved.citation_metadata["doi"], "10.1000/example")

    @override_settings(
        RAG_PRIMARY_COUNT=3,
        RAG_CONTEXT_COUNT=5,
        RAG_MAX_CONTEXT_CHARS=5000,
        RAG_MAX_CHUNKS_PER_SOURCE=3,
    )
    @patch("apps.qa.services.fetch_neighbors", return_value=[])
    @patch("apps.qa.services.fetch_lexical_candidates")
    @patch("apps.qa.services.fetch_vector_candidates")
    @patch("apps.qa.services.openai_client")
    @patch("apps.qa.services.knowledge_config")
    def test_retrieval_combines_transcripts_consultations_and_articles(
        self, config_mock, client_mock, vector_mock, lexical_mock, _neighbors_mock,
    ):
        config_mock.return_value = SimpleNamespace(
            embedding_model="text-embedding-3-small",
            embedding_dimensions=1536,
            similarity_threshold=.45,
            retrieval_count=5,
            youtube_enabled=True,
            consultations_enabled=True,
            journal_articles_enabled=True,
        )
        client_mock.return_value.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1, 0.2])]
        )
        vector_mock.return_value = [
            chunk(1, .82, source="video-1", title="Surgery video", source_type="youtube_video"),
            chunk(2, .80, source="case-1", title="Clinical case", source_type="consultation"),
            chunk(3, .84, source="article-1", title="Journal study", source_type="journal_article"),
        ]
        lexical_mock.return_value = []

        results = retrieve_chunks("veterinary surgery evidence")

        self.assertEqual(
            {item.source_type for item in results},
            {"youtube_video", "consultation", "journal_article"},
        )
