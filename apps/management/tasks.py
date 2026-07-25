import hashlib
import html
import re

import tiktoken
from celery import shared_task
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from openai import OpenAI

from apps.qa.models import KnowledgeProcessingRun, KnowledgeSourceProfile, RagSourceDocument


def normalize_source_text(value):
    text = html.unescape(str(value or ""))
    replacements = {
        "ā€™": "’",
        "â€™": "’",
        "â€œ": "“",
        "â€\x9d": "”",
        "â€“": "–",
        "â€”": "—",
        "Â ": " ",
    }
    for broken, corrected in replacements.items():
        text = text.replace(broken, corrected)
    for _ in range(2):
        try:
            repaired = text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if repaired == text:
            break
        text = repaired
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_chunks(text, chunk_size, overlap):
    if overlap >= chunk_size:
        raise ValueError("Chunk overlap must be smaller than chunk size.")
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
        tokens = encoding.encode(text)
        decode = encoding.decode
    except Exception:
        tokens = re.findall(r"\S+\s*", text)
        decode = lambda values: "".join(values)
    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_text = decode(tokens[start:end]).strip()
        if chunk_text:
            chunks.append((len(chunks), chunk_text, end - start))
        if end == len(tokens):
            break
        start = end - overlap
    return chunks


def _embed_chunks(chunks, model, dimensions):
    client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=90, max_retries=4)
    embeddings = []
    batch_size = 50
    for offset in range(0, len(chunks), batch_size):
        batch = chunks[offset : offset + batch_size]
        response = client.embeddings.create(
            model=model,
            dimensions=dimensions,
            input=[item[1] for item in batch],
        )
        embeddings.extend(item.embedding for item in response.data)
    return embeddings


def _replace_chunks(source, chunks, embeddings, model, content_hash):
    metadata = {
        "source_document_id": source.pk,
        "source_type": source.source_type,
        "content_hash": content_hash,
        "source_updated_at": source.updated_at.isoformat() if source.updated_at else "",
    }
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM rag_chunks WHERE source_document_id = %s AND embedding_model = %s",
            [source.pk, model],
        )
        for (index, text, token_count), embedding in zip(chunks, embeddings):
            cursor.execute(
                """
                INSERT INTO rag_chunks (
                    source_document_id, source_type, source_id, source_title, source_url,
                    chunk_index, chunk_text, chunk_token_count, embedding, embedding_model,
                    content_hash, metadata, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s, NOW(), NOW()
                )
                ON CONFLICT (source_type, source_id, chunk_index, embedding_model)
                DO UPDATE SET
                    source_document_id = EXCLUDED.source_document_id,
                    source_title = EXCLUDED.source_title,
                    source_url = EXCLUDED.source_url,
                    chunk_text = EXCLUDED.chunk_text,
                    chunk_token_count = EXCLUDED.chunk_token_count,
                    embedding = EXCLUDED.embedding,
                    content_hash = EXCLUDED.content_hash,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """,
                [
                    source.pk, source.source_type, source.source_id, source.source_title, source.source_url,
                    index, text, token_count, embedding, model, content_hash, metadata,
                ],
            )


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def process_knowledge_source(self, run_id):
    run = KnowledgeProcessingRun.objects.select_related("profile").get(pk=run_id)
    profile = run.profile
    run.status = KnowledgeProcessingRun.Status.PROCESSING
    run.started_at = timezone.now()
    run.error_details = ""
    run.save(update_fields=("status", "started_at", "error_details"))
    profile.status = KnowledgeSourceProfile.Status.PROCESSING
    profile.save(update_fields=("status", "updated_at"))

    try:
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not configured.")
        source = RagSourceDocument.objects.get(pk=profile.source_document_id)
        cleaned = normalize_source_text(source.content)
        if not cleaned:
            raise ValueError("The source has no content to process.")
        chunks = split_chunks(cleaned, run.chunk_size, run.chunk_overlap)
        if not chunks:
            raise ValueError("No searchable chunks were produced.")
        embeddings = _embed_chunks(chunks, run.embedding_model, run.embedding_dimensions)
        content_hash = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
        _replace_chunks(source, chunks, embeddings, run.embedding_model, content_hash)

        source.content = cleaned
        source.extraction_status = "success"
        source.updated_at = timezone.now()
        source.save(update_fields=("content", "extraction_status", "updated_at"))
        run.status = KnowledgeProcessingRun.Status.COMPLETED
        run.chunk_count = len(chunks)
        run.completed_at = timezone.now()
        run.save(update_fields=("status", "chunk_count", "completed_at"))
        profile.status = KnowledgeSourceProfile.Status.COMPLETED
        profile.error_message = ""
        profile.save(update_fields=("status", "error_message", "updated_at"))
        return {"source_id": source.pk, "chunks": len(chunks)}
    except Exception as exc:
        message = str(exc)[:4000]
        if self.request.retries < self.max_retries:
            run.status = KnowledgeProcessingRun.Status.PENDING
            run.error_details = f"Retry {self.request.retries + 1} scheduled: {message}"
            run.save(update_fields=("status", "error_details"))
            profile.status = KnowledgeSourceProfile.Status.PENDING
            profile.error_message = run.error_details
            profile.save(update_fields=("status", "error_message", "updated_at"))
            raise self.retry(exc=exc)
        run.status = KnowledgeProcessingRun.Status.FAILED
        run.error_details = message
        run.completed_at = timezone.now()
        run.save(update_fields=("status", "error_details", "completed_at"))
        profile.status = KnowledgeSourceProfile.Status.FAILED
        profile.error_message = message
        profile.save(update_fields=("status", "error_message", "updated_at"))
        raise
