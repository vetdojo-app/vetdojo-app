import hashlib
import html
import re

import tiktoken
from celery import shared_task
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from openai import OpenAI
from psycopg.types.json import Jsonb

from apps.qa.models import (
    JournalArticleDetails,
    JournalArticlePage,
    KnowledgeProcessingRun,
    KnowledgeSourceProfile,
    RagSourceDocument,
)

from .pdf_extraction import analyze_article_metadata, extract_pdf_pages, pages_as_source_text


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


def split_article_pages(pages, chunk_size, overlap, article):
    """Create globally ordered chunks without crossing PDF page boundaries."""
    chunks = []
    citation = {
        "article_title": article.article_title,
        "authors": article.authors,
        "journal": article.journal,
        "publication_year": article.publication_year,
        "volume": article.volume,
        "issue": article.issue,
        "article_pages": article.article_pages,
        "doi": article.doi,
    }
    for page in pages:
        if not page.cleaned_text.strip():
            continue
        for _, text, token_count in split_chunks(page.cleaned_text, chunk_size, overlap):
            metadata = {
                **citation,
                "pdf_page_start": page.pdf_page_number,
                "pdf_page_end": page.pdf_page_number,
                "printed_page_start": page.printed_page_number,
                "printed_page_end": page.printed_page_number,
            }
            chunks.append((len(chunks), text, token_count, metadata))
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
    base_metadata = {
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
        for chunk, embedding in zip(chunks, embeddings):
            index, text, token_count = chunk[:3]
            metadata = {**base_metadata, **(chunk[3] if len(chunk) > 3 else {})}
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
                    index, text, token_count, embedding, model, content_hash, Jsonb(metadata),
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
        if source.source_type == "journal_article":
            article = JournalArticleDetails.objects.get(profile=profile)
            pages = list(article.pages.order_by("pdf_page_number"))
            if source.extraction_status != "success" or not pages:
                raise ValueError("Extract the journal PDF before generating searchable chunks.")
            chunks = split_article_pages(pages, run.chunk_size, run.chunk_overlap, article)
        else:
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
        update_fields = ["status", "error_message", "updated_at"]
        if run.publish_on_success:
            profile.is_active = True
            profile.approved_by = run.initiated_by
            profile.approved_at = timezone.now()
            update_fields.extend(("is_active", "approved_by", "approved_at"))
        profile.save(update_fields=update_fields)
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


@shared_task
def extract_journal_article(article_id, use_openai=True):
    article = JournalArticleDetails.objects.select_related("profile").get(pk=article_id)
    source = RagSourceDocument.objects.get(pk=article.profile.source_document_id)
    source.extraction_status = "processing"
    source.save(update_fields=("extraction_status", "updated_at"))
    article.metadata_analysis_status = (
        JournalArticleDetails.MetadataAnalysisStatus.PENDING
        if use_openai else JournalArticleDetails.MetadataAnalysisStatus.NOT_REQUESTED
    )
    article.extraction_warning = ""
    article.save(update_fields=("metadata_analysis_status", "extraction_warning", "updated_at"))

    try:
        with article.pdf_file.open("rb") as handle:
            pages = extract_pdf_pages(handle)
        source_text = pages_as_source_text(pages)
        if not source_text.strip():
            raise ValueError("No extractable journal text was produced.")

        suggestions = {}
        warning_messages = [warning for page in pages for warning in page.warnings]
        method = JournalArticleDetails.ExtractionMethod.LOCAL
        analysis_status = JournalArticleDetails.MetadataAnalysisStatus.NOT_REQUESTED
        if use_openai:
            try:
                suggestions = analyze_article_metadata(article)
                warning_messages.extend(suggestions.get("quality_warnings", []))
                method = JournalArticleDetails.ExtractionMethod.HYBRID
                analysis_status = JournalArticleDetails.MetadataAnalysisStatus.COMPLETED
            except Exception as exc:
                warning_messages.append(f"OpenAI metadata analysis was unavailable: {str(exc)[:300]}")
                analysis_status = JournalArticleDetails.MetadataAnalysisStatus.FAILED

        with transaction.atomic():
            JournalArticlePage.objects.filter(article=article).delete()
            JournalArticlePage.objects.bulk_create([
                JournalArticlePage(
                    article=article,
                    pdf_page_number=page.pdf_page_number,
                    printed_page_number=page.printed_page_number,
                    raw_text=page.raw_text,
                    cleaned_text=page.cleaned_text,
                    text_char_count=len(page.cleaned_text),
                    warnings=list(page.warnings),
                )
                for page in pages
            ])
            source.content = source_text
            source.extraction_status = "success"
            source.updated_at = timezone.now()
            source.save(update_fields=("content", "extraction_status", "updated_at"))
            article.pdf_page_count = len(pages)
            article.extraction_method = method
            article.suggested_metadata = suggestions
            article.metadata_analysis_status = analysis_status
            article.extraction_warning = "\n".join(dict.fromkeys(warning_messages))
            article.extracted_at = timezone.now()
            article.save(update_fields=(
                "pdf_page_count", "extraction_method", "suggested_metadata",
                "metadata_analysis_status", "extraction_warning", "extracted_at", "updated_at",
            ))
        return {"article_id": article.pk, "pages": len(pages), "characters": len(source_text)}
    except Exception as exc:
        source.extraction_status = "failed"
        source.updated_at = timezone.now()
        source.save(update_fields=("extraction_status", "updated_at"))
        article.extraction_warning = str(exc)[:1000]
        article.metadata_analysis_status = JournalArticleDetails.MetadataAnalysisStatus.FAILED
        article.save(update_fields=("extraction_warning", "metadata_analysis_status", "updated_at"))
        raise
