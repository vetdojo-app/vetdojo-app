from django.contrib import admin
from django.db.models import Count
from django.urls import reverse
from django.utils.html import format_html

from .models import (
    AIAnswerCache,
    Answer,
    AnswerSource,
    Feedback,
    JournalArticleDetails,
    KnowledgeBaseSettings,
    KnowledgeCategory,
    KnowledgeProcessingRun,
    KnowledgeSourceProfile,
    Question,
)


class AnswerSourceInline(admin.TabularInline):
    model = AnswerSource
    extra = 0
    fields = ("citation_order", "source_title", "chunk_index", "similarity", "citation_metadata", "source_link")
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    @admin.display(description="Source")
    def source_link(self, obj):
        if not obj.source_url:
            return "—"
        return format_html('<a href="{}" target="_blank" rel="noopener">Open video</a>', obj.source_url)

    def has_add_permission(self, request, obj=None):
        return False


class FeedbackInline(admin.TabularInline):
    model = Feedback
    extra = 0
    fields = ("user", "helpful", "comment", "created_at")
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class AnswerInline(admin.StackedInline):
    model = Answer
    extra = 0
    fields = ("source_type", "model_name", "text", "duration_ms", "created_at", "answer_link")
    readonly_fields = fields
    can_delete = False

    @admin.display(description="Record")
    def answer_link(self, obj):
        if not obj.pk:
            return "—"
        url = reverse("admin:qa_answer_change", args=[obj.pk])
        return format_html('<a href="{}">View answer details</a>', url)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("short_question", "user", "status", "answer_count", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("text", "user__username", "user__email", "error_message")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"
    list_select_related = ("user",)
    inlines = (AnswerInline,)
    ordering = ("-created_at",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user").annotate(_answer_count=Count("answers"))

    @admin.display(description="Question")
    def short_question(self, obj):
        return obj.text if len(obj.text) <= 90 else f"{obj.text[:87]}…"

    @admin.display(description="Answers", ordering="_answer_count")
    def answer_count(self, obj):
        return obj._answer_count


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ("id", "question_link", "source_type", "model_name", "source_count", "duration", "created_at")
    list_filter = ("source_type", "model_name", "prompt_version", "created_at")
    search_fields = ("text", "question__text", "question__user__email")
    readonly_fields = ("created_at", "token_total")
    date_hierarchy = "created_at"
    list_select_related = ("question", "question__user")
    inlines = (AnswerSourceInline, FeedbackInline)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("question").annotate(_source_count=Count("sources"))

    @admin.display(description="Question", ordering="question__text")
    def question_link(self, obj):
        url = reverse("admin:qa_question_change", args=[obj.question_id])
        label = obj.question.text[:65] + ("…" if len(obj.question.text) > 65 else "")
        return format_html('<a href="{}">{}</a>', url, label)

    @admin.display(description="Sources", ordering="_source_count")
    def source_count(self, obj):
        return obj._source_count

    @admin.display(description="Latency", ordering="duration_ms")
    def duration(self, obj):
        return f"{obj.duration_ms / 1000:.2f}s"

    @admin.display(description="Total tokens")
    def token_total(self, obj):
        return obj.input_tokens + obj.output_tokens


@admin.register(AnswerSource)
class AnswerSourceAdmin(admin.ModelAdmin):
    list_display = ("source_title", "answer", "chunk_index", "similarity_score", "citation_order")
    list_filter = ("answer__source_type",)
    search_fields = ("source_title", "source_id", "excerpt", "answer__question__text")
    readonly_fields = ("answer", "chunk_id", "source_document_id", "source_id", "source_title", "source_url", "chunk_index", "similarity", "excerpt", "citation_metadata", "citation_order")
    list_select_related = ("answer",)

    @admin.display(description="Similarity", ordering="similarity")
    def similarity_score(self, obj):
        return f"{obj.similarity:.1%}"

    def has_add_permission(self, request):
        return False


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("answer", "user", "helpful", "short_comment", "created_at")
    list_filter = ("helpful", "created_at")
    search_fields = ("comment", "user__email", "answer__question__text")
    readonly_fields = ("user", "answer", "helpful", "comment", "created_at")
    date_hierarchy = "created_at"
    list_select_related = ("answer", "user")

    @admin.display(description="Comment")
    def short_comment(self, obj):
        return obj.comment[:70] + ("…" if len(obj.comment) > 70 else "")

    def has_add_permission(self, request):
        return False


@admin.register(AIAnswerCache)
class AIAnswerCacheAdmin(admin.ModelAdmin):
    list_display = ("short_question", "model_name", "prompt_version", "is_active", "reuse_count", "expires_at")
    list_filter = ("is_active", "model_name", "prompt_version", "expires_at")
    search_fields = ("normalized_question", "question_hash", "answer_text")
    readonly_fields = ("question_hash", "normalized_question", "structured_content", "answer_text", "model_name", "prompt_version", "input_tokens", "output_tokens", "reuse_count", "created_at", "last_used_at", "expires_at")
    actions = ("activate_entries", "deactivate_entries")

    @admin.display(description="Question")
    def short_question(self, obj):
        return obj.normalized_question[:85] + ("…" if len(obj.normalized_question) > 85 else "")

    @admin.action(description="Activate selected cache entries")
    def activate_entries(self, request, queryset):
        queryset.update(is_active=True)

    @admin.action(description="Deactivate selected cache entries")
    def deactivate_entries(self, request, queryset):
        queryset.update(is_active=False)

    def has_add_permission(self, request):
        return False


admin.site.register(KnowledgeCategory)
admin.site.register(KnowledgeBaseSettings)
admin.site.register(KnowledgeSourceProfile)
admin.site.register(KnowledgeProcessingRun)
admin.site.register(JournalArticleDetails)
