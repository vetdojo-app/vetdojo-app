from django.conf import settings
from django.db import models

from .pdf_validation import validate_journal_pdf
from .storage import PrivateMediaStorage


class RagSourceDocument(models.Model):
    id = models.BigAutoField(primary_key=True)
    video_id = models.TextField(blank=True, null=True)
    video_url = models.TextField(blank=True, null=True)
    title = models.TextField(blank=True, null=True)
    source_type = models.TextField()
    source_id = models.TextField()
    source_title = models.TextField(blank=True, null=True)
    source_url = models.TextField(blank=True, null=True)
    content_type = models.TextField(default="transcript")
    content = models.TextField(blank=True, null=True)
    transcript_language = models.TextField(blank=True, null=True)
    transcript_type = models.TextField(blank=True, null=True)
    extraction_status = models.TextField(default="pending")
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = "rag_source_documents"


class RagChunk(models.Model):
    id = models.BigIntegerField(primary_key=True)
    source_document_id = models.BigIntegerField(blank=True, null=True)
    source_type = models.TextField()
    source_id = models.TextField()
    source_title = models.TextField(blank=True, null=True)
    source_url = models.TextField(blank=True, null=True)
    chunk_index = models.IntegerField()
    chunk_text = models.TextField()
    chunk_token_count = models.IntegerField()
    embedding_model = models.TextField()
    content_hash = models.TextField()
    metadata = models.JSONField(default=dict)

    class Meta:
        managed = False
        db_table = "rag_chunks"


class Question(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETE = "complete", "Complete"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="questions")
    text = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class Answer(models.Model):
    class SourceType(models.TextChoices):
        CLIENT = "client_content", "VetDojo knowledge base"
        AI_AUTO = "general_ai_automatic", "General AI knowledge"
        AI_REQUESTED = "general_ai_requested", "Requested AI alternative"
        INSUFFICIENT = "insufficient", "Insufficient information"

    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="answers")
    text = models.TextField()
    structured_content = models.JSONField(default=dict, blank=True)
    source_type = models.CharField(max_length=32, choices=SourceType.choices)
    model_name = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=40, default="v1")
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    @property
    def is_client_content(self):
        return self.source_type == self.SourceType.CLIENT

    @property
    def display_limitations(self):
        limitations = (self.structured_content or {}).get("limitations", [])
        if isinstance(limitations, str):
            return [limitations] if limitations.strip() else []
        if isinstance(limitations, list):
            if limitations and all(isinstance(item, str) and len(item) <= 1 for item in limitations):
                restored = "".join(limitations).strip()
                return [restored] if restored else []
            return [str(item) for item in limitations if str(item).strip()]
        return []


class AnswerSource(models.Model):
    answer = models.ForeignKey(Answer, on_delete=models.CASCADE, related_name="sources")
    chunk_id = models.BigIntegerField()
    source_document_id = models.BigIntegerField(blank=True, null=True)
    source_type = models.CharField(max_length=40, default="youtube_video")
    source_id = models.CharField(max_length=255)
    source_title = models.TextField(blank=True)
    source_url = models.URLField(max_length=1000, blank=True)
    chunk_index = models.IntegerField()
    similarity = models.FloatField()
    excerpt = models.TextField(blank=True)
    citation_metadata = models.JSONField(default=dict, blank=True)
    citation_order = models.PositiveIntegerField()

    class Meta:
        ordering = ["citation_order"]
        constraints = [models.UniqueConstraint(fields=["answer", "chunk_id"], name="unique_answer_chunk")]


class Feedback(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    answer = models.ForeignKey(Answer, on_delete=models.CASCADE, related_name="feedback")
    helpful = models.BooleanField()
    comment = models.CharField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "answer"], name="unique_user_answer_feedback")]


class AIAnswerCache(models.Model):
    question_hash = models.CharField(max_length=64)
    normalized_question = models.TextField()
    structured_content = models.JSONField(default=dict)
    answer_text = models.TextField()
    model_name = models.CharField(max_length=100)
    prompt_version = models.CharField(max_length=40)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    reuse_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["question_hash", "model_name", "prompt_version"],
                name="unique_ai_cache_version",
            )
        ]
        indexes = [models.Index(fields=["question_hash", "is_active", "expires_at"])]

    def __str__(self):
        return f"{self.normalized_question[:80]} ({self.model_name})"


class KnowledgeSourceProfile(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        NEEDS_REPROCESSING = "needs_reprocessing", "Needs reprocessing"
        INACTIVE = "inactive", "Inactive"

    source_document_id = models.BigIntegerField(unique=True)
    category = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    is_active = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="created_knowledge_sources")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="updated_knowledge_sources")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="approved_knowledge_sources")
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Knowledge source {self.source_document_id}"


class KnowledgeCategory(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)
        verbose_name_plural = "knowledge categories"

    def __str__(self):
        return self.name


class KnowledgeBaseSettings(models.Model):
    embedding_model = models.CharField(max_length=100, default="text-embedding-3-small")
    embedding_dimensions = models.PositiveIntegerField(default=1536)
    chat_model = models.CharField(max_length=100, default="gpt-4.1-mini")
    retrieval_count = models.PositiveIntegerField(default=10)
    similarity_threshold = models.FloatField(default=0.48)
    chunk_size = models.PositiveIntegerField(default=900)
    chunk_overlap = models.PositiveIntegerField(default=120)
    ai_fallback_enabled = models.BooleanField(default=True)
    youtube_enabled = models.BooleanField(default=True)
    consultations_enabled = models.BooleanField(default=True)
    journal_articles_enabled = models.BooleanField(default=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "knowledge-base settings"

    def __str__(self):
        return "Knowledge-base settings"


class ConsultationDetails(models.Model):
    profile = models.OneToOneField(KnowledgeSourceProfile, on_delete=models.CASCADE, related_name="consultation")
    case_reference = models.CharField(max_length=120)
    species = models.CharField(max_length=80, blank=True)
    breed = models.CharField(max_length=120, blank=True)
    age = models.CharField(max_length=80, blank=True)
    sex = models.CharField(max_length=40, blank=True)
    presentation_reason = models.TextField(blank=True)
    history = models.TextField(blank=True)
    examination = models.TextField(blank=True)
    assessment = models.TextField(blank=True)
    treatment_options = models.TextField(blank=True)
    proposed_plan = models.TextField(blank=True)
    prognosis = models.TextField(blank=True)
    consultation_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.case_reference


class JournalArticleDetails(models.Model):
    class ExtractionMethod(models.TextChoices):
        PENDING = "pending", "Not processed"
        LOCAL = "local", "Local PDF extraction"
        OPENAI_ASSISTED = "openai_assisted", "OpenAI-assisted extraction"
        HYBRID = "hybrid", "Hybrid extraction"

    class MetadataAnalysisStatus(models.TextChoices):
        NOT_REQUESTED = "not_requested", "Not requested"
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    profile = models.OneToOneField(
        KnowledgeSourceProfile,
        on_delete=models.CASCADE,
        related_name="journal_article",
    )
    pdf_file = models.FileField(
        upload_to="journal_articles/%Y/%m/",
        storage=PrivateMediaStorage(),
        validators=[validate_journal_pdf],
    )
    original_filename = models.CharField(max_length=255)
    file_sha256 = models.CharField(max_length=64, unique=True, null=True, blank=True)
    article_title = models.TextField()
    authors = models.TextField(blank=True)
    journal = models.CharField(max_length=255, blank=True)
    publication_year = models.PositiveSmallIntegerField(null=True, blank=True)
    volume = models.CharField(max_length=40, blank=True)
    issue = models.CharField(max_length=40, blank=True)
    article_pages = models.CharField(max_length=80, blank=True)
    doi = models.CharField(max_length=255, blank=True)
    publisher_url = models.URLField(max_length=1000, blank=True)
    abstract = models.TextField(blank=True)
    pdf_page_count = models.PositiveIntegerField(null=True, blank=True)
    extraction_method = models.CharField(
        max_length=24,
        choices=ExtractionMethod.choices,
        default=ExtractionMethod.PENDING,
    )
    metadata_reviewed = models.BooleanField(default=False)
    suggested_metadata = models.JSONField(default=dict, blank=True)
    metadata_analysis_status = models.CharField(
        max_length=16,
        choices=MetadataAnalysisStatus.choices,
        default=MetadataAnalysisStatus.NOT_REQUESTED,
    )
    extraction_warning = models.TextField(blank=True)
    extracted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "journal article details"
        verbose_name_plural = "journal article details"

    def __str__(self):
        return self.article_title or self.original_filename


class JournalArticlePage(models.Model):
    article = models.ForeignKey(
        JournalArticleDetails,
        on_delete=models.CASCADE,
        related_name="pages",
    )
    pdf_page_number = models.PositiveIntegerField()
    printed_page_number = models.CharField(max_length=20, blank=True)
    raw_text = models.TextField()
    cleaned_text = models.TextField()
    text_char_count = models.PositiveIntegerField(default=0)
    warnings = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ("pdf_page_number",)
        constraints = [
            models.UniqueConstraint(
                fields=("article", "pdf_page_number"),
                name="unique_journal_article_pdf_page",
            )
        ]

    def __str__(self):
        return f"{self.article} - PDF page {self.pdf_page_number}"


class KnowledgeProcessingRun(models.Model):
    class Action(models.TextChoices):
        INITIAL = "initial", "Initial processing"
        REPROCESS = "reprocess", "Reprocessing"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    profile = models.ForeignKey(KnowledgeSourceProfile, on_delete=models.CASCADE, related_name="processing_runs")
    action = models.CharField(max_length=16, choices=Action.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    chunk_count = models.PositiveIntegerField(default=0)
    embedding_model = models.CharField(max_length=100)
    embedding_dimensions = models.PositiveIntegerField(default=1536)
    chunk_size = models.PositiveIntegerField(default=900)
    chunk_overlap = models.PositiveIntegerField(default=120)
    estimated_tokens = models.PositiveIntegerField(default=0)
    error_details = models.TextField(blank=True)
    publish_on_success = models.BooleanField(default=False)
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
