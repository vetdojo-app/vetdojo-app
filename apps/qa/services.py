import json
import hashlib
import logging
import re
import time
from dataclasses import dataclass, replace
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone
from openai import OpenAI

from .models import AIAnswerCache, Answer, AnswerSource, KnowledgeBaseSettings, Question

LOGGER = logging.getLogger(__name__)
STOP_WORDS = {
    "about", "after", "also", "are", "considerations", "could", "does", "for", "from", "have",
    "how", "into", "key", "should", "that", "the", "their", "this", "what", "when", "where",
    "which", "with", "would", "young",
}
GENERIC_KEYWORD_WEIGHTS = {"dog": 0.2, "cat": 0.2, "animal": 0.2, "repair": 0.45, "surgery": 0.35, "surgical": 0.35}


def knowledge_config():
    config = KnowledgeBaseSettings.objects.first()
    if config:
        return config
    return KnowledgeBaseSettings(
        embedding_model=settings.OPENAI_EMBEDDING_MODEL,
        embedding_dimensions=settings.OPENAI_EMBEDDING_DIMENSIONS,
        chat_model=settings.OPENAI_CHAT_MODEL,
        retrieval_count=settings.RAG_CONTEXT_COUNT,
        similarity_threshold=settings.RAG_MIN_RELEVANCE,
        chunk_size=settings.RAG_CHUNK_SIZE,
        chunk_overlap=settings.RAG_CHUNK_OVERLAP,
    )


def enabled_source_types(config):
    values = []
    if config.youtube_enabled:
        values.extend(("youtube_video", "youtube_transcript"))
    if config.consultations_enabled:
        values.append("consultation")
    return values


class QAConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    source_type: str
    source_id: str
    source_title: str
    source_url: str
    chunk_index: int
    chunk_text: str
    similarity: float
    metadata: dict
    lexical_score: float = 0.0
    relevance_score: float = 0.0


def normalize_metadata(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except (json.JSONDecodeError, TypeError):
            LOGGER.warning("Ignoring malformed RAG chunk metadata")
    return {}


def clean_model_text(value):
    text = str(value or "")
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.DOTALL)
    return text.strip()


def normalize_cache_question(question):
    normalized = " ".join((question or "").casefold().split())
    return re.sub(r"[?!.]+$", "", normalized).strip()


def cache_question_hash(question):
    return hashlib.sha256(normalize_cache_question(question).encode("utf-8")).hexdigest()


def get_cached_ai_answer(question):
    config = knowledge_config()
    normalized = normalize_cache_question(question)
    if not normalized:
        return None
    cached = AIAnswerCache.objects.filter(
        question_hash=cache_question_hash(question),
        normalized_question=normalized,
        model_name=config.chat_model,
        prompt_version=settings.AI_PROMPT_VERSION,
        is_active=True,
        expires_at__gt=timezone.now(),
    ).first()
    if cached:
        AIAnswerCache.objects.filter(pk=cached.pk).update(reuse_count=F("reuse_count") + 1, last_used_at=timezone.now())
    return cached


def cache_ai_answer(question, content, input_tokens=0, output_tokens=0):
    config = knowledge_config()
    normalized = normalize_cache_question(question)
    if not normalized:
        return None
    defaults = {
        "normalized_question": normalized,
        "structured_content": content,
        "answer_text": content_as_text(content),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reuse_count": 0,
        "is_active": True,
        "expires_at": timezone.now() + timedelta(days=settings.AI_CACHE_TTL_DAYS),
    }
    cached, _ = AIAnswerCache.objects.update_or_create(
        question_hash=cache_question_hash(question),
        model_name=config.chat_model,
        prompt_version=settings.AI_PROMPT_VERSION,
        defaults=defaults,
    )
    return cached


def openai_client():
    if not settings.OPENAI_API_KEY:
        raise QAConfigurationError("OPENAI_API_KEY is not configured.")
    return OpenAI(api_key=settings.OPENAI_API_KEY, timeout=60.0, max_retries=2)


def extract_keywords(question):
    words = re.findall(r"[a-z0-9]+", question.lower())
    keywords = []
    for word in words:
        if len(word) < 3 or word in STOP_WORDS:
            continue
        # Small normalization helps fracture/repair terminology without changing medical meaning.
        normalized = {"repairing": "repair", "fractures": "fracture", "dogs": "dog", "puppies": "puppy"}.get(word, word)
        if normalized not in keywords:
            keywords.append(normalized)
    return keywords[:10]


def row_to_chunk(row, similarity=None):
    return RetrievedChunk(
        chunk_id=row["chunk_id"], source_type=row.get("source_type") or "youtube_video",
        source_id=row.get("source_id") or "", source_title=row.get("source_title") or "",
        source_url=row.get("source_url") or "", chunk_index=row.get("chunk_index") or 0,
        chunk_text=row.get("chunk_text") or "", similarity=float(similarity if similarity is not None else row.get("similarity", 0) or 0),
        metadata=normalize_metadata(row.get("metadata")),
    )


def fetch_vector_candidates(embedding, config=None):
    config = config or knowledge_config()
    source_types = enabled_source_types(config)
    if not source_types:
        return []
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.id AS chunk_id, c.source_type, c.source_id, c.source_title, c.source_url,
                   c.chunk_index, c.chunk_text, 1 - (c.embedding <=> %s::vector) AS similarity,
                   c.metadata
            FROM rag_chunks c
            INNER JOIN qa_knowledgesourceprofile p ON p.source_document_id = c.source_document_id
            WHERE p.is_active = TRUE AND c.embedding_model = %s AND c.source_type = ANY(%s)
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
            """,
            [embedding, config.embedding_model, source_types, embedding, settings.RAG_CANDIDATE_COUNT],
        )
        columns = [column[0] for column in cursor.description]
        return [row_to_chunk(dict(zip(columns, row))) for row in cursor.fetchall()]


def fetch_lexical_candidates(keywords, embedding, config=None):
    config = config or knowledge_config()
    source_types = enabled_source_types(config)
    if not keywords or not source_types:
        return []
    clauses, match_params, score_parts, score_params = [], [], [], []
    for keyword in keywords:
        clauses.append("(c.source_title ILIKE %s OR c.chunk_text ILIKE %s)")
        pattern = f"%{keyword}%"
        match_params.extend([pattern, pattern])
        weight = GENERIC_KEYWORD_WEIGHTS.get(keyword, 1.0)
        score_parts.append("(CASE WHEN c.source_title ILIKE %s THEN %s ELSE 0 END + CASE WHEN c.chunk_text ILIKE %s THEN %s ELSE 0 END)")
        score_params.extend([pattern, weight * 3, pattern, weight])
    query = f"""
        SELECT c.id AS chunk_id, c.source_type, c.source_id, c.source_title, c.source_url,
               c.chunk_index, c.chunk_text, c.metadata, 1 - (c.embedding <=> %s::vector) AS similarity,
               ({' + '.join(score_parts)}) AS lexical_rank
        FROM rag_chunks c
        INNER JOIN qa_knowledgesourceprofile p ON p.source_document_id = c.source_document_id
        WHERE p.is_active = TRUE AND c.embedding_model = %s AND c.source_type = ANY(%s)
          AND ({' OR '.join(clauses)})
        ORDER BY lexical_rank DESC, c.updated_at DESC
        LIMIT %s
    """
    params = [embedding] + score_params + [config.embedding_model, source_types] + match_params + [settings.RAG_LEXICAL_COUNT]
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        columns = [column[0] for column in cursor.description]
        return [row_to_chunk(dict(zip(columns, row))) for row in cursor.fetchall()]


def lexical_relevance(chunk, keywords):
    if not keywords:
        return 0.0
    title = chunk.source_title.lower()
    text = chunk.chunk_text.lower()
    total_weight = sum(GENERIC_KEYWORD_WEIGHTS.get(word, 1.0) for word in keywords)
    title_weight = sum(GENERIC_KEYWORD_WEIGHTS.get(word, 1.0) for word in keywords if word in title)
    text_weight = sum(GENERIC_KEYWORD_WEIGHTS.get(word, 1.0) for word in keywords if word in text)
    return min(1.0, (title_weight * 1.15 + text_weight * 0.35) / max(1.0, total_weight))


def rank_candidates(candidates, keywords):
    merged = {}
    for chunk in candidates:
        current = merged.get(chunk.chunk_id)
        if current is None or chunk.similarity > current.similarity:
            merged[chunk.chunk_id] = chunk
    ranked = []
    for chunk in merged.values():
        lexical = lexical_relevance(chunk, keywords)
        # Vector meaning remains primary; explicit clinical/title matches prevent unrelated false positives.
        relevance = min(1.0, chunk.similarity * 0.68 + lexical * 0.55)
        ranked.append(replace(chunk, lexical_score=lexical, relevance_score=relevance))
    return sorted(ranked, key=lambda item: (item.relevance_score, item.similarity), reverse=True)


def fetch_neighbors(primary_chunks, config=None):
    config = config or knowledge_config()
    neighbors = []
    with connection.cursor() as cursor:
        for primary in primary_chunks:
            cursor.execute(
                """
                SELECT c.id AS chunk_id, c.source_type, c.source_id, c.source_title, c.source_url,
                       c.chunk_index, c.chunk_text, c.metadata
                FROM rag_chunks c
                INNER JOIN qa_knowledgesourceprofile p ON p.source_document_id = c.source_document_id
                WHERE p.is_active = TRUE AND c.source_type = %s AND c.source_id = %s
                  AND c.embedding_model = %s AND c.chunk_index BETWEEN %s AND %s
                ORDER BY c.chunk_index
                """,
                [primary.source_type, primary.source_id, config.embedding_model, max(0, primary.chunk_index - 1), primary.chunk_index + 1],
            )
            columns = [column[0] for column in cursor.description]
            for row in cursor.fetchall():
                chunk = row_to_chunk(dict(zip(columns, row)), similarity=primary.similarity)
                neighbors.append(replace(chunk, lexical_score=primary.lexical_score, relevance_score=max(0, primary.relevance_score - 0.04)))
    return neighbors


def select_context(candidates):
    selected, seen, source_counts, used_chars = [], set(), {}, 0
    for chunk in candidates:
        source_key = (chunk.source_type, chunk.source_id)
        if chunk.chunk_id in seen or source_counts.get(source_key, 0) >= settings.RAG_MAX_CHUNKS_PER_SOURCE:
            continue
        if used_chars + len(chunk.chunk_text) > settings.RAG_MAX_CONTEXT_CHARS:
            continue
        selected.append(chunk)
        seen.add(chunk.chunk_id)
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        used_chars += len(chunk.chunk_text)
        if len(selected) >= settings.RAG_CONTEXT_COUNT:
            break
    return selected


def retrieve_chunks(question):
    config = knowledge_config()
    embedding = openai_client().embeddings.create(
        model=config.embedding_model, input=[question], dimensions=config.embedding_dimensions,
    ).data[0].embedding
    keywords = extract_keywords(question)
    ranked = rank_candidates(fetch_vector_candidates(embedding, config) + fetch_lexical_candidates(keywords, embedding, config), keywords)
    primaries = [c for c in ranked if c.relevance_score >= config.similarity_threshold][:settings.RAG_PRIMARY_COUNT]
    expanded = rank_candidates(primaries + fetch_neighbors(primaries, config), keywords)
    return select_context(expanded)[:config.retrieval_count]


def evidence_is_sufficient(chunks):
    return bool(chunks and chunks[0].relevance_score >= knowledge_config().similarity_threshold)


def parse_model_json(raw):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"evidence_sufficient": False, "summary": raw.strip(), "sections": [], "limitations": [], "cited_sources": []}
    cited = []
    for value in data.get("cited_sources", []):
        try:
            number = int(value)
            if number > 0 and number not in cited:
                cited.append(number)
        except (TypeError, ValueError):
            continue
    raw_limitations = data.get("limitations", [])
    if isinstance(raw_limitations, str):
        cleaned = clean_model_text(raw_limitations)
        limitations = [cleaned] if cleaned else []
    elif isinstance(raw_limitations, list):
        if raw_limitations and all(isinstance(item, str) and len(item) <= 1 for item in raw_limitations):
            restored = "".join(raw_limitations).strip()
            restored = clean_model_text(restored)
            limitations = [restored] if restored else []
        else:
            limitations = [clean_model_text(item) for item in raw_limitations if clean_model_text(item)]
    else:
        limitations = []
    return {
        "evidence_sufficient": bool(data.get("evidence_sufficient", False)),
        "summary": clean_model_text(data.get("summary", "")),
        "sections": [
            {"heading": clean_model_text(section.get("heading", "")), "content": clean_model_text(section.get("content", ""))}
            for section in data.get("sections", [])
            if isinstance(section, dict) and clean_model_text(section.get("content", ""))
        ],
        "limitations": limitations,
        "cited_sources": cited,
    }


def generate_content(question, chunks=None):
    config = knowledge_config()
    chunks = chunks or []
    grounded = bool(chunks)
    if grounded:
        context = "\n\n".join(
            f"[SOURCE {i}] Title: {c.source_title}\nChunk ID: {c.chunk_id}\nContent: {c.chunk_text}"
            for i, c in enumerate(chunks, 1)
        )
        system = (
            "You are a veterinary clinical education assistant using excerpts from the VetDojo knowledge base, which may include specialist videos and de-identified consultations. "
            "First decide whether the excerpts directly and materially answer the question. Unrelated procedural content is insufficient. "
            "If insufficient, set evidence_sufficient=false and do not manufacture an answer. If sufficient, set it true and provide a detailed, "
            "well-organized answer using only supported facts. Aim for 500-900 words when the evidence supports that depth; be shorter when it does not. "
            "Use practical headings such as assessment/planning, approach, fixation, growth-plate considerations, intraoperative checks, postoperative care, "
            "and cautions only where supported. Cite claims inline as [Source N]. Return cited_sources as the source numbers actually used. "
            "Return JSON with evidence_sufficient (boolean), summary (string), sections (array of heading/content objects), limitations (array), and cited_sources (array of integers)."
        )
        user = f"Clinical question: {question}\n\nVetDojo excerpts:\n{context}"
    else:
        system = (
            "You are a veterinary clinical education assistant using general AI knowledge. Provide a detailed, well-structured educational answer, "
            "normally 500-900 words for a broad clinical question. Be explicit about uncertainty and avoid patient-specific directives. "
            "Return JSON with evidence_sufficient=true, summary, sections (heading/content objects), limitations, and cited_sources as an empty array."
        )
        user = f"Clinical question: {question}"
    response = openai_client().chat.completions.create(
        model=config.chat_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"}, temperature=0.2,
    )
    content = parse_model_json(response.choices[0].message.content or "")
    usage = response.usage
    return content, getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0)


def cited_chunks(content, chunks):
    valid_numbers = [number for number in content.get("cited_sources", []) if number <= len(chunks)]
    if not content.get("evidence_sufficient") or not valid_numbers:
        return []
    # One visible card per supporting video; inline source numbers remain in the answer.
    selected, source_ids = [], set()
    for number in valid_numbers:
        chunk = chunks[number - 1]
        source_key = (chunk.source_type, chunk.source_id)
        if source_key not in source_ids:
            selected.append(chunk)
            source_ids.add(source_key)
    return selected


def content_as_text(content):
    parts = [content.get("summary", "")]
    parts.extend(f"{s.get('heading', '')}\n{s.get('content', '')}" for s in content.get("sections", []))
    if content.get("limitations"):
        parts.append("Limitations\n" + "\n".join(content["limitations"]))
    return "\n\n".join(part for part in parts if part.strip())


@transaction.atomic
def save_answer(question, content, source_type, chunks, started_at, input_tokens=0, output_tokens=0):
    config = knowledge_config()
    answer = Answer.objects.create(
        question=question, text=content_as_text(content), structured_content=content,
        source_type=source_type, model_name=config.chat_model,
        prompt_version=settings.AI_PROMPT_VERSION,
        input_tokens=input_tokens, output_tokens=output_tokens,
        duration_ms=int((time.monotonic() - started_at) * 1000),
    )
    for order, chunk in enumerate(chunks, 1):
        AnswerSource.objects.create(
            answer=answer, chunk_id=chunk.chunk_id,
            source_document_id=normalize_metadata(chunk.metadata).get("source_document_id"),
            source_type=chunk.source_type,
            source_id=chunk.source_id,
            source_title=chunk.source_title or ("Clinical case" if chunk.source_type == "consultation" else "Untitled video"),
            source_url=chunk.source_url or "", chunk_index=chunk.chunk_index,
            similarity=chunk.relevance_score or chunk.similarity, excerpt=chunk.chunk_text[:700], citation_order=order,
        )
    return answer


def answer_question(question_record):
    started = time.monotonic()
    try:
        chunks = retrieve_chunks(question_record.text)
        grounded_content = None
        total_input = total_output = 0
        if evidence_is_sufficient(chunks):
            grounded_content, input_tokens, output_tokens = generate_content(question_record.text, chunks)
            total_input += input_tokens
            total_output += output_tokens
        sources = cited_chunks(grounded_content or {}, chunks)
        if grounded_content and sources:
            content, source_type, used_chunks = grounded_content, Answer.SourceType.CLIENT, sources
        else:
            if not knowledge_config().ai_fallback_enabled:
                content = {
                    "evidence_sufficient": False,
                    "summary": "The active VetDojo knowledge base does not contain enough relevant information to answer this question.",
                    "sections": [],
                    "limitations": ["General AI fallback is disabled."],
                    "cited_sources": [],
                }
                answer = save_answer(
                    question_record, content, Answer.SourceType.INSUFFICIENT, [], started, total_input, total_output
                )
                question_record.status = Question.Status.COMPLETE
                question_record.error_message = ""
                question_record.save(update_fields=["status", "error_message", "updated_at"])
                return answer
            cached = get_cached_ai_answer(question_record.text)
            if cached:
                content = cached.structured_content
            else:
                content, input_tokens, output_tokens = generate_content(question_record.text)
                total_input += input_tokens
                total_output += output_tokens
                cache_ai_answer(question_record.text, content, input_tokens, output_tokens)
            source_type, used_chunks = Answer.SourceType.AI_AUTO, []
        answer = save_answer(question_record, content, source_type, used_chunks, started, total_input, total_output)
        question_record.status = Question.Status.COMPLETE
        question_record.error_message = ""
        question_record.save(update_fields=["status", "error_message", "updated_at"])
        return answer
    except Exception as exc:
        LOGGER.exception("Question processing failed question_id=%s", question_record.pk)
        question_record.status = Question.Status.FAILED
        question_record.error_message = str(exc)[:1000]
        question_record.save(update_fields=["status", "error_message", "updated_at"])
        raise


def generate_ai_alternative(question_record):
    existing = question_record.answers.filter(source_type=Answer.SourceType.AI_REQUESTED).first()
    if existing:
        return existing
    started = time.monotonic()
    content, input_tokens, output_tokens = generate_content(question_record.text)
    return save_answer(question_record, content, Answer.SourceType.AI_REQUESTED, [], started, input_tokens, output_tokens)
