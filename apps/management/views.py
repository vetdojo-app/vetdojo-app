import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.db.models import Exists, OuterRef, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from apps.qa.models import (
    ConsultationDetails,
    JournalArticleDetails,
    KnowledgeBaseSettings,
    KnowledgeCategory,
    KnowledgeProcessingRun,
    KnowledgeSourceProfile,
    RagChunk,
    RagSourceDocument,
)

from apps.qa.services import generate_content, retrieve_chunks

from .forms import (
    ConsultationForm,
    JournalArticleForm,
    KnowledgeBaseSettingsForm,
    KnowledgeCategoryForm,
    ManagementAuthenticationForm,
    ManagementPasswordChangeForm,
    ProfileForm,
    RetrievalTestForm,
    SourceForm,
)
from .tasks import extract_journal_article, process_knowledge_source


def has_management_access(user):
    return user.is_authenticated and (user.is_superuser or user.groups.filter(name="ADMIN").exists())


management_required = user_passes_test(has_management_access, login_url="management:login")


def management_login(request):
    if has_management_access(request.user):
        return redirect("management:dashboard")
    form = ManagementAuthenticationForm(request=request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        next_url = request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return redirect(next_url)
        return redirect("management:dashboard")
    return render(request, "management/login.html", {"form": form})


@management_required
def management_logout(request):
    if request.method == "POST":
        logout(request)
        return redirect("management:login")
    return redirect("management:dashboard")


@management_required
def management_password_change(request):
    form = ManagementPasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Your management password was changed successfully.")
        return redirect("management:dashboard")
    return render(request, "management/password_change.html", {"form": form})


@management_required
def dashboard(request):
    profiles = KnowledgeSourceProfile.objects.all()
    config = KnowledgeBaseSettings.objects.first()
    context = {
        "transcript_count": 0, "consultation_count": 0, "article_count": 0,
        "source_count": 0, "chunk_count": 0,
        "active_count": profiles.filter(is_active=True).count(),
        "pending_count": profiles.filter(status=KnowledgeSourceProfile.Status.PENDING).count(),
        "failed_count": profiles.filter(status=KnowledgeSourceProfile.Status.FAILED).count(),
        "recent_runs": KnowledgeProcessingRun.objects.select_related("profile")[:6],
        "recent_errors": KnowledgeProcessingRun.objects.filter(status=KnowledgeProcessingRun.Status.FAILED)[:5],
        "embedding_model": config.embedding_model if config else settings.OPENAI_EMBEDDING_MODEL,
        "embedding_dimensions": config.embedding_dimensions if config else settings.OPENAI_EMBEDDING_DIMENSIONS,
    }
    try:
        context.update(
            transcript_count=RagSourceDocument.objects.filter(
                source_type__in=("youtube_video", "youtube_transcript")
            ).count(),
            consultation_count=RagSourceDocument.objects.filter(source_type="consultation").count(),
            article_count=RagSourceDocument.objects.filter(source_type="journal_article").count(),
            source_count=RagSourceDocument.objects.count(), chunk_count=RagChunk.objects.count(),
        )
    except DatabaseError:
        context["rag_unavailable"] = True
    last_run = KnowledgeProcessingRun.objects.filter(status=KnowledgeProcessingRun.Status.COMPLETED).first()
    context["last_success"] = last_run.completed_at if last_run else None
    return render(request, "management/dashboard.html", context)


@management_required
def source_list(request):
    kind = request.GET.get("type", "").strip()
    status = request.GET.get("status", "").strip()
    query = request.GET.get("q", "").strip()
    page_number = request.GET.get("page", "1")

    try:
        sources = RagSourceDocument.objects.only(
            "id", "source_type", "source_id", "source_title", "source_url",
            "extraction_status", "updated_at",
        ).order_by("-id")
        if kind:
            sources = sources.filter(source_type=kind)
        if query:
            sources = sources.filter(Q(source_title__icontains=query) | Q(source_id__icontains=query))
        if status:
            matching_ids = KnowledgeSourceProfile.objects.filter(status=status).values("source_document_id")
            sources = sources.filter(id__in=matching_ids)

        page_obj = Paginator(sources, 25).get_page(page_number)
        page_range = page_obj.paginator.get_elided_page_range(
            page_obj.number, on_each_side=2, on_ends=1
        )
        page_rows = list(page_obj.object_list)
        source_ids = [source.pk for source in page_rows]
        profiles = {
            profile.source_document_id: profile
            for profile in KnowledgeSourceProfile.objects.filter(
                source_document_id__in=source_ids
            ).annotate(
                has_processing_runs=Exists(
                    KnowledgeProcessingRun.objects.filter(profile_id=OuterRef("pk"))
                )
            )
        }
        article_profiles = {
            article.profile_id: article
            for article in JournalArticleDetails.objects.filter(profile_id__in=[profile.pk for profile in profiles.values()])
        }
        for source in page_rows:
            source.profile_record = profiles.get(source.pk)
            source.article_record = article_profiles.get(source.profile_record.pk) if source.profile_record else None
    except DatabaseError:
        page_obj = None
        page_range = []
        page_rows = []
        messages.error(request, "The RAG source table is unavailable on this database.")
    return render(request, "management/source_list.html", {
        "sources": page_rows,
        "page_obj": page_obj,
        "page_range": page_range,
        "statuses": KnowledgeSourceProfile.Status.choices,
        "selected_type": kind,
        "selected_status": status,
        "search_query": query,
    })


@management_required
def source_edit(request, pk=None, source_kind=None):
    source = get_object_or_404(RagSourceDocument, pk=pk) if pk else None
    if source and source.source_type == "journal_article":
        return redirect("management:article_edit", pk=source.pk)
    previous_content = source.content if source else None
    previous_identity = (source.source_title, source.source_url) if source else None
    profile = KnowledgeSourceProfile.objects.filter(source_document_id=pk).first() if pk else None
    kind = source.source_type if source else source_kind
    consultation = ConsultationDetails.objects.filter(profile=profile).first() if profile and kind == "consultation" else None
    source_form = SourceForm(request.POST or None, instance=source, source_kind=kind)
    profile_form = ProfileForm(request.POST or None, instance=profile)
    consultation_form = ConsultationForm(
        request.POST or None,
        instance=consultation,
        initial={"prepared_text": source.content if source and kind == "consultation" else ""},
    ) if kind == "consultation" else None
    valid = source_form.is_valid() and profile_form.is_valid() and (consultation_form is None or consultation_form.is_valid())
    if request.method == "POST" and valid:
        with transaction.atomic():
            saved_source = source_form.save(commit=False)
            saved_source.source_type = kind
            saved_source.title = saved_source.source_title
            if consultation_form:
                saved_source.video_id = f"consultation:{saved_source.source_id}"
                saved_source.video_url = saved_source.source_url or ""
                saved_source.content_type = "consultation"
                saved_source.content = consultation_form.searchable_document()
                saved_source.transcript_type = "structured_consultation"
            else:
                saved_source.video_id = saved_source.source_id
                saved_source.video_url = saved_source.source_url or ""
                saved_source.content_type = "transcript"
            saved_source.extraction_status = "success" if saved_source.content else "pending"
            saved_source.created_at = saved_source.created_at or timezone.now()
            saved_source.updated_at = timezone.now()
            saved_source.save()
            saved_profile = profile_form.save(commit=False)
            saved_profile.source_document_id = saved_source.pk
            saved_profile.created_by = saved_profile.created_by or request.user
            saved_profile.updated_by = request.user
            if source and (
                previous_content != saved_source.content
                or previous_identity != (saved_source.source_title, saved_source.source_url)
            ):
                saved_profile.status = KnowledgeSourceProfile.Status.NEEDS_REPROCESSING
            saved_profile.save()
            if consultation_form:
                detail = consultation_form.save(commit=False)
                detail.profile = saved_profile
                detail.save()
        messages.success(request, "Knowledge source saved. Processing has not started.")
        return redirect("management:source_list")
    return render(request, "management/source_form.html", {
        "source_form": source_form, "profile_form": profile_form, "consultation_form": consultation_form,
        "source": source, "kind": kind,
    })


def _article_metadata(article):
    return {
        "article_title": article.article_title,
        "authors": article.authors,
        "journal": article.journal,
        "publication_year": article.publication_year,
        "volume": article.volume,
        "issue": article.issue,
        "article_pages": article.article_pages,
        "doi": article.doi,
        "publisher_url": article.publisher_url,
    }


def _article_source_url(article):
    if article.publisher_url:
        return article.publisher_url
    return f"https://doi.org/{article.doi}" if article.doi else ""


def _safe_upload_name(value):
    return Path(str(value or "").replace("\\", "/")).name


def _temporary_article_title(uploaded_file):
    stem = Path(_safe_upload_name(getattr(uploaded_file, "name", ""))).stem
    return stem.replace("_", " ").replace("-", " ").strip() or "Untitled journal article"


def _create_processing_run(profile, source, user, *, publish_on_success=False):
    config = KnowledgeBaseSettings.objects.first()
    action = KnowledgeProcessingRun.Action.REPROCESS if profile.processing_runs.exists() else KnowledgeProcessingRun.Action.INITIAL
    run = KnowledgeProcessingRun.objects.create(
        profile=profile,
        action=action,
        embedding_model=config.embedding_model if config else settings.OPENAI_EMBEDDING_MODEL,
        embedding_dimensions=config.embedding_dimensions if config else settings.OPENAI_EMBEDDING_DIMENSIONS,
        chunk_size=config.chunk_size if config else settings.RAG_CHUNK_SIZE,
        chunk_overlap=config.chunk_overlap if config else settings.RAG_CHUNK_OVERLAP,
        estimated_tokens=max(1, len((source.content or "")) // 4),
        initiated_by=user,
        publish_on_success=publish_on_success,
    )
    profile.status = KnowledgeSourceProfile.Status.PENDING
    profile.error_message = ""
    profile.updated_by = user
    if publish_on_success:
        profile.is_active = False
        profile.approved_by = None
        profile.approved_at = None
    profile.save()
    process_knowledge_source.delay(run.pk)
    return run


@management_required
def article_edit(request, pk=None):
    source = get_object_or_404(RagSourceDocument, pk=pk, source_type="journal_article") if pk else None
    profile = KnowledgeSourceProfile.objects.filter(source_document_id=pk).first() if pk else None
    article = JournalArticleDetails.objects.filter(profile=profile).first() if profile else None
    if source and (not profile or not article):
        raise Http404("Journal article details are unavailable.")

    previous_citation_metadata = (
        article.article_title,
        article.authors,
        article.journal,
        article.publication_year,
        article.volume,
        article.issue,
        article.article_pages,
        article.doi,
        article.publisher_url,
    ) if article else None
    article_form = JournalArticleForm(request.POST or None, request.FILES or None, instance=article)
    profile_form = ProfileForm(request.POST or None, instance=profile)

    action = request.POST.get("action", "save_draft")
    forms_valid = article_form.is_valid() and profile_form.is_valid() if request.method == "POST" else False
    if forms_valid and action == "publish":
        if "pdf_file" in request.FILES:
            article_form.add_error("pdf_file", "Analyze the replacement PDF before publishing it.")
            forms_valid = False
        if not (article_form.cleaned_data.get("article_title") or "").strip():
            article_form.add_error("article_title", "Enter or apply the article title before publishing.")
            forms_valid = False
        if not article_form.cleaned_data.get("metadata_reviewed"):
            article_form.add_error("metadata_reviewed", "Confirm that you reviewed the article details before publishing.")
            forms_valid = False
        if not source or source.extraction_status != "success" or not article or not article.pages.exists():
            messages.error(request, "Analyze the PDF successfully before publishing it.")
            forms_valid = False

    if (
        forms_valid
        and action == "analyze"
        and source
        and source.extraction_status in {"queued", "processing"}
        and source.updated_at
        and source.updated_at >= timezone.now() - timedelta(minutes=30)
    ):
        messages.warning(request, "PDF analysis is already running. This page will update automatically.")
        return redirect("management:article_edit", pk=source.pk)

    if request.method == "POST" and forms_valid:
        replacement_uploaded = "pdf_file" in request.FILES
        with transaction.atomic():
            saved_article = article_form.save(commit=False)
            inspection = getattr(article_form, "pdf_inspection", None)
            if not (saved_article.article_title or "").strip():
                saved_article.article_title = _temporary_article_title(request.FILES.get("pdf_file"))
            if saved_article.doi and not saved_article.publisher_url:
                saved_article.publisher_url = f"https://doi.org/{saved_article.doi}"

            if source is None:
                source = RagSourceDocument(
                    source_type="journal_article",
                    source_id=f"journal:{uuid.uuid4().hex}",
                    content_type="journal_article",
                    extraction_status="pending",
                    content="",
                    created_at=timezone.now(),
                )
            source.source_title = saved_article.article_title
            source.title = saved_article.article_title
            source.source_url = _article_source_url(saved_article)
            source.metadata = _article_metadata(saved_article)
            source.updated_at = timezone.now()
            source.save()

            saved_profile = profile_form.save(commit=False)
            saved_profile.source_document_id = source.pk
            saved_profile.created_by = saved_profile.created_by or request.user
            saved_profile.updated_by = request.user

            current_citation_metadata = (
                saved_article.article_title,
                saved_article.authors,
                saved_article.journal,
                saved_article.publication_year,
                saved_article.volume,
                saved_article.issue,
                saved_article.article_pages,
                saved_article.doi,
                saved_article.publisher_url,
            )
            citation_changed = previous_citation_metadata is not None and previous_citation_metadata != current_citation_metadata
            was_processed = bool(
                profile and (
                    profile.processing_runs.exists()
                    or profile.status in {
                        KnowledgeSourceProfile.Status.COMPLETED,
                        KnowledgeSourceProfile.Status.INACTIVE,
                        KnowledgeSourceProfile.Status.NEEDS_REPROCESSING,
                    }
                )
            )
            if (replacement_uploaded or citation_changed) and was_processed:
                saved_profile.status = KnowledgeSourceProfile.Status.NEEDS_REPROCESSING
                saved_profile.is_active = False
                saved_profile.approved_by = None
                saved_profile.approved_at = None
            saved_profile.save()

            saved_article.profile = saved_profile
            if replacement_uploaded and inspection:
                saved_article.original_filename = _safe_upload_name(request.FILES["pdf_file"].name)
                saved_article.file_sha256 = inspection.sha256
                saved_article.pdf_page_count = inspection.page_count
                saved_article.extraction_method = JournalArticleDetails.ExtractionMethod.PENDING
                saved_article.extraction_warning = ""
                saved_article.suggested_metadata = {}
                saved_article.metadata_analysis_status = JournalArticleDetails.MetadataAnalysisStatus.NOT_REQUESTED
                saved_article.extracted_at = None
                source.content = ""
                source.extraction_status = "pending"
                source.save(update_fields=("content", "extraction_status", "updated_at"))
            saved_article.save()
            if replacement_uploaded:
                saved_article.pages.all().delete()

        if action == "analyze":
            source.extraction_status = "queued"
            source.save(update_fields=("extraction_status", "updated_at"))
            saved_article.metadata_reviewed = False
            saved_article.metadata_analysis_status = (
                JournalArticleDetails.MetadataAnalysisStatus.PENDING
                if settings.OPENAI_PDF_ANALYSIS_ENABLED
                else JournalArticleDetails.MetadataAnalysisStatus.NOT_REQUESTED
            )
            saved_article.extraction_warning = ""
            saved_article.save(update_fields=(
                "metadata_reviewed", "metadata_analysis_status", "extraction_warning", "updated_at",
            ))
            extract_journal_article.delay(saved_article.pk, use_openai=settings.OPENAI_PDF_ANALYSIS_ENABLED)
            messages.success(request, "PDF uploaded. Analysis has started and this page will update automatically.")
            return redirect("management:article_edit", pk=source.pk)

        if action == "publish":
            if saved_profile.processing_runs.filter(
                status__in=(KnowledgeProcessingRun.Status.PENDING, KnowledgeProcessingRun.Status.PROCESSING)
            ).exists():
                messages.warning(request, "This article already has an active publishing job.")
            else:
                _create_processing_run(saved_profile, source, request.user, publish_on_success=True)
                messages.success(request, "Article approved. Search indexing and publishing have started.")
            return redirect("management:article_edit", pk=source.pk)

        messages.success(request, "Journal article saved as a draft.")
        return redirect("management:article_edit", pk=source.pk)

    return render(request, "management/article_form.html", {
        "article_form": article_form,
        "profile_form": profile_form,
        "source": source,
        "article": article,
        "processing_run": profile.processing_runs.first() if profile else None,
    })


@management_required
def article_download(request, pk):
    profile = get_object_or_404(KnowledgeSourceProfile, source_document_id=pk)
    article = get_object_or_404(JournalArticleDetails, profile=profile)
    try:
        file_handle = article.pdf_file.open("rb")
    except (FileNotFoundError, OSError) as exc:
        raise Http404("The private PDF file is unavailable.") from exc
    response = FileResponse(
        file_handle,
        as_attachment=True,
        filename=_safe_upload_name(article.original_filename),
        content_type="application/pdf",
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@management_required
def queue_article_extraction(request, pk):
    if request.method != "POST":
        return redirect("management:source_list")
    profile = get_object_or_404(KnowledgeSourceProfile, source_document_id=pk)
    article = get_object_or_404(JournalArticleDetails, profile=profile)
    source = get_object_or_404(RagSourceDocument, pk=pk, source_type="journal_article")
    if source.extraction_status in {"queued", "processing"}:
        messages.warning(request, "This article already has a pending or active extraction job.")
        return redirect("management:article_edit", pk=pk)

    use_openai = settings.OPENAI_PDF_ANALYSIS_ENABLED and request.POST.get("use_openai", "1") == "1"
    source.extraction_status = "queued"
    source.save(update_fields=("extraction_status", "updated_at"))
    article.metadata_analysis_status = (
        JournalArticleDetails.MetadataAnalysisStatus.PENDING
        if use_openai else JournalArticleDetails.MetadataAnalysisStatus.NOT_REQUESTED
    )
    article.extraction_warning = ""
    article.save(update_fields=("metadata_analysis_status", "extraction_warning", "updated_at"))
    extract_journal_article.delay(article.pk, use_openai=use_openai)
    messages.success(request, "PDF extraction queued. Refresh this page to see the result.")
    return redirect("management:article_edit", pk=pk)


@management_required
def queue_processing(request, pk):
    if request.method != "POST":
        return redirect("management:source_list")
    profile = get_object_or_404(KnowledgeSourceProfile, source_document_id=pk)
    try:
        source = RagSourceDocument.objects.get(pk=pk)
    except RagSourceDocument.DoesNotExist as exc:
        raise Http404("Knowledge source not found.") from exc
    if getattr(source, "source_type", "") == "journal_article":
        article = get_object_or_404(JournalArticleDetails, profile=profile)
        if source.extraction_status != "success" or not article.pages.exists():
            messages.error(request, "Extract this PDF successfully before generating searchable chunks.")
            return redirect("management:article_edit", pk=pk)
        if not article.metadata_reviewed:
            messages.error(request, "Confirm the article metadata before generating citation-ready chunks.")
            return redirect("management:article_edit", pk=pk)
    if profile.processing_runs.filter(
        status__in=(KnowledgeProcessingRun.Status.PENDING, KnowledgeProcessingRun.Status.PROCESSING)
    ).exists():
        messages.warning(request, "This source already has a pending or active processing job.")
        return redirect("management:source_list")
    _create_processing_run(profile, source, request.user)
    messages.success(request, "Processing request queued. Progress is available in Processing history.")
    return redirect("management:source_list")


@management_required
def toggle_source(request, pk):
    if request.method == "POST":
        profile = get_object_or_404(KnowledgeSourceProfile, source_document_id=pk)
        if not profile.is_active:
            has_chunks = RagChunk.objects.filter(source_document_id=pk).exists()
            if profile.status not in (
                KnowledgeSourceProfile.Status.COMPLETED,
                KnowledgeSourceProfile.Status.INACTIVE,
            ) or not has_chunks:
                messages.error(request, "Process this source successfully before activating it.")
                return redirect("management:source_list")
        profile.is_active = not profile.is_active
        profile.status = KnowledgeSourceProfile.Status.COMPLETED if profile.is_active else KnowledgeSourceProfile.Status.INACTIVE
        profile.approved_by = request.user if profile.is_active else None
        profile.approved_at = timezone.now() if profile.is_active else None
        profile.updated_by = request.user
        profile.save()
        messages.success(request, "Source activation updated.")
    return redirect("management:source_list")


@management_required
def run_list(request):
    runs = KnowledgeProcessingRun.objects.select_related("profile", "initiated_by")
    return render(request, "management/run_list.html", {"runs": runs})


@management_required
def source_chunks(request, pk):
    source = get_object_or_404(RagSourceDocument, pk=pk)
    try:
        chunks = RagChunk.objects.filter(source_document_id=pk).order_by("embedding_model", "chunk_index")
        chunk_rows = list(chunks)
    except DatabaseError:
        chunk_rows = []
        messages.error(request, "Chunk data is unavailable on this database.")
    return render(request, "management/source_chunks.html", {"source": source, "chunks": chunk_rows})


@management_required
def source_delete(request, pk):
    source = get_object_or_404(RagSourceDocument, pk=pk)
    profile = KnowledgeSourceProfile.objects.filter(source_document_id=pk).first()
    if request.method == "POST":
        with transaction.atomic():
            if profile:
                profile.delete()
            source.delete()
        messages.success(request, "The source and its generated chunks were permanently deleted.")
        return redirect("management:source_list")
    return render(request, "management/source_confirm_delete.html", {"source": source})


@management_required
def category_list(request):
    return render(request, "management/category_list.html", {"categories": KnowledgeCategory.objects.all()})


@management_required
def category_edit(request, pk=None):
    category = get_object_or_404(KnowledgeCategory, pk=pk) if pk else None
    form = KnowledgeCategoryForm(request.POST or None, instance=category)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Category saved.")
        return redirect("management:category_list")
    return render(request, "management/category_form.html", {"form": form, "category": category})


@management_required
def knowledge_settings(request):
    config = KnowledgeBaseSettings.objects.first() or KnowledgeBaseSettings(
        embedding_model=settings.OPENAI_EMBEDDING_MODEL,
        embedding_dimensions=settings.OPENAI_EMBEDDING_DIMENSIONS,
        chat_model=settings.OPENAI_CHAT_MODEL,
        retrieval_count=settings.RAG_CONTEXT_COUNT,
        similarity_threshold=settings.RAG_MIN_RELEVANCE,
        chunk_size=settings.RAG_CHUNK_SIZE,
        chunk_overlap=settings.RAG_CHUNK_OVERLAP,
    )
    previous_processing_config = (
        config.embedding_model, config.embedding_dimensions, config.chunk_size, config.chunk_overlap
    )
    form = KnowledgeBaseSettingsForm(request.POST or None, instance=config)
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.updated_by = request.user
        saved.save()
        current_processing_config = (
            saved.embedding_model, saved.embedding_dimensions, saved.chunk_size, saved.chunk_overlap
        )
        if saved.pk and previous_processing_config != current_processing_config:
            KnowledgeSourceProfile.objects.filter(
                status=KnowledgeSourceProfile.Status.COMPLETED
            ).update(status=KnowledgeSourceProfile.Status.NEEDS_REPROCESSING)
            messages.warning(request, "Processing configuration changed. Completed sources now require reprocessing.")
        else:
            messages.success(request, "Knowledge-base settings saved.")
        return redirect("management:settings")
    return render(request, "management/settings.html", {"form": form})


@management_required
def retrieval_test(request):
    form = RetrievalTestForm(request.POST or None)
    chunks = []
    preview = None
    fallback_required = None
    if request.method == "POST" and form.is_valid():
        try:
            chunks = retrieve_chunks(form.cleaned_data["question"])
            fallback_required = not bool(chunks)
            if form.cleaned_data["generate_preview"]:
                preview, _, _ = generate_content(form.cleaned_data["question"], chunks)
        except Exception as exc:
            messages.error(request, f"Retrieval test failed: {str(exc)[:500]}")
    return render(request, "management/retrieval_test.html", {
        "form": form, "chunks": chunks, "preview": preview, "fallback_required": fallback_required,
    })
