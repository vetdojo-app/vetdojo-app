from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm

from apps.qa.models import (
    ConsultationDetails,
    JournalArticleDetails,
    KnowledgeBaseSettings,
    KnowledgeCategory,
    KnowledgeSourceProfile,
    RagSourceDocument,
)
from apps.qa.pdf_validation import inspect_journal_pdf


class SourceForm(forms.ModelForm):
    class Meta:
        model = RagSourceDocument
        fields = ("source_title", "source_id", "source_url", "transcript_language", "transcript_type", "content", "metadata")
        widgets = {
            "source_title": forms.TextInput(attrs={"placeholder": "e.g. Canine TPLO postoperative complications"}),
            "source_id": forms.TextInput(attrs={"placeholder": "YouTube video ID, e.g. dQw4w9WgXcQ"}),
            "source_url": forms.URLInput(attrs={"placeholder": "https://www.youtube.com/watch?v=..."}),
            "transcript_language": forms.TextInput(attrs={"placeholder": "e.g. en"}),
            "transcript_type": forms.TextInput(attrs={"placeholder": "e.g. manual, youtube, or browser_visible"}),
            "content": forms.Textarea(attrs={"rows": 16, "placeholder": "Paste the complete, reviewed transcript here. Do not include timestamps unless they are useful to the content."}),
            "metadata": forms.Textarea(attrs={"rows": 4, "placeholder": '{"speaker": "Dr Smith", "specialty": "Surgery"}'}),
        }

    def __init__(self, *args, source_kind=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_kind = source_kind
        if source_kind == "consultation":
            self.fields["source_title"].label = "Safe case title"
            self.fields["source_title"].widget.attrs["placeholder"] = "e.g. Canine chronic cough case review"
            self.fields["source_id"].label = "Case source ID"
            self.fields["source_id"].widget.attrs["placeholder"] = "Unique anonymous ID, e.g. CONSULT-2026-014"
            self.fields["source_url"].label = "Reference URL (optional)"
            self.fields["source_url"].widget.attrs["placeholder"] = "Optional internal reference URL"
            self.fields.pop("content")

    def clean_source_id(self):
        source_id = self.cleaned_data["source_id"].strip()
        queryset = RagSourceDocument.objects.filter(source_type=self.source_kind, source_id=source_id)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError("This source ID is already used for this source type.")
        return source_id

    def clean(self):
        cleaned = super().clean()
        if self.source_kind != "consultation" and not (cleaned.get("content") or "").strip():
            self.add_error("content", "A transcript is required before this source can be saved.")
        return cleaned


class ProfileForm(forms.ModelForm):
    category = forms.ChoiceField(required=False)

    class Meta:
        model = KnowledgeSourceProfile
        fields = ("category", "notes")
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Internal notes for administrators; these are not included in search."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        names = list(KnowledgeCategory.objects.filter(is_active=True).values_list("name", flat=True))
        current = self.instance.category if self.instance and self.instance.pk else ""
        if current and current not in names:
            names.append(current)
        self.fields["category"].choices = [("", "Select a category")] + [(name, name) for name in names]


class ConsultationForm(forms.ModelForm):
    prepared_text = forms.CharField(
        required=False,
        label="Prepared consultation text",
        help_text="Optional. When supplied, this reviewed text becomes the searchable document instead of the generated structured summary.",
        widget=forms.Textarea(attrs={
            "rows": 12,
            "placeholder": "Paste an existing de-identified, reviewed consultation summary here.",
        }),
    )

    class Meta:
        model = ConsultationDetails
        exclude = ("profile",)
        widgets = {
            "case_reference": forms.TextInput(attrs={"placeholder": "Anonymous reference, e.g. CASE-2026-014"}),
            "species": forms.TextInput(attrs={"placeholder": "e.g. Dog"}),
            "breed": forms.TextInput(attrs={"placeholder": "e.g. Labrador Retriever"}),
            "age": forms.TextInput(attrs={"placeholder": "e.g. 7 years"}),
            "sex": forms.TextInput(attrs={"placeholder": "e.g. Female, spayed"}),
            "consultation_date": forms.DateInput(attrs={"type": "date"}),
            "presentation_reason": forms.Textarea(attrs={"rows": 3, "placeholder": "Primary complaint and reason for referral."}),
            "history": forms.Textarea(attrs={"rows": 3, "placeholder": "Relevant clinical history, duration, prior treatment and response. Exclude owner-identifying information."}),
            "examination": forms.Textarea(attrs={"rows": 3, "placeholder": "Pertinent physical examination and diagnostic findings."}),
            "assessment": forms.Textarea(attrs={"rows": 3, "placeholder": "Clinical assessment, problem list and differential diagnoses."}),
            "treatment_options": forms.Textarea(attrs={"rows": 3, "placeholder": "Options discussed, including important benefits and risks."}),
            "proposed_plan": forms.Textarea(attrs={"rows": 3, "placeholder": "Recommended diagnostics, treatment and follow-up plan."}),
            "prognosis": forms.Textarea(attrs={"rows": 3, "placeholder": "Expected outcome and factors affecting prognosis."}),
        }

    def normalized_document(self):
        labels = (
            ("Case reference", "case_reference"), ("Species", "species"), ("Breed", "breed"), ("Age", "age"),
            ("Sex", "sex"), ("Presentation reason", "presentation_reason"), ("History", "history"),
            ("Examination", "examination"), ("Assessment", "assessment"), ("Treatment options", "treatment_options"),
            ("Proposed plan", "proposed_plan"), ("Prognosis", "prognosis"),
        )
        return "\n\n".join(f"{label}:\n{self.cleaned_data.get(field)}" for label, field in labels if self.cleaned_data.get(field))

    def searchable_document(self):
        prepared = normalize_text_encoding(self.cleaned_data.get("prepared_text", ""))
        return prepared or normalize_text_encoding(self.normalized_document())


class JournalArticleForm(forms.ModelForm):
    class Meta:
        model = JournalArticleDetails
        fields = (
            "pdf_file", "article_title", "authors", "journal", "publication_year",
            "volume", "issue", "article_pages", "doi", "publisher_url", "abstract",
            "metadata_reviewed",
        )
        help_texts = {
            "pdf_file": (
                "Text-based PDF only, up to 25 MB. The original file remains private and is not shared with public users."
            ),
            "article_pages": "Printed journal page range, for example 602-606.",
            "doi": "Optional. Usually printed on the first page, for example 10.1111/example.12345.",
            "publisher_url": "Optional. Leave empty when a DOI is available; its link will be created automatically.",
            "metadata_reviewed": "Required only when publishing. Confirm that you reviewed the article details and suggestions.",
        }
        widgets = {
            # The current private PDF is shown through the protected management
            # download view; rendering ClearableFileInput would request storage.url().
            "pdf_file": forms.FileInput(attrs={"accept": "application/pdf,.pdf"}),
            "article_title": forms.TextInput(attrs={"placeholder": "Full article title"}),
            "authors": forms.Textarea(attrs={"rows": 2, "placeholder": "e.g. Guillemot A, Rouvre G, Autefage A, et al."}),
            "journal": forms.TextInput(attrs={"placeholder": "e.g. Veterinary Surgery"}),
            "publication_year": forms.NumberInput(attrs={"placeholder": "e.g. 2014", "min": 1800, "max": 2100}),
            "volume": forms.TextInput(attrs={"placeholder": "e.g. 43"}),
            "issue": forms.TextInput(attrs={"placeholder": "e.g. 6"}),
            "article_pages": forms.TextInput(attrs={"placeholder": "e.g. 602-606"}),
            "doi": forms.TextInput(attrs={"placeholder": "e.g. 10.1111/j.1532-950X.2014.12300.x"}),
            "publisher_url": forms.URLInput(attrs={"placeholder": "Optional publisher or article URL"}),
            "abstract": forms.Textarea(attrs={"rows": 5, "placeholder": "Optional article abstract"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["article_title"].required = False
        self.fields["article_title"].label = "Article title (optional during upload)"
        self.fields["authors"].label = "Authors (recommended)"
        self.fields["journal"].label = "Journal (recommended)"
        self.fields["publication_year"].label = "Publication year (recommended)"
        self.fields["volume"].label = "Volume (optional)"
        self.fields["issue"].label = "Issue (optional)"
        self.fields["article_pages"].label = "Printed page range (optional)"
        self.fields["doi"].label = "DOI (optional)"
        self.fields["publisher_url"].label = "Article link (optional)"
        self.fields["abstract"].label = "Abstract (optional)"
        self.fields["metadata_reviewed"].label = "I have reviewed these article details"
        if self.instance and self.instance.pk:
            self.fields["pdf_file"].required = False
            self.fields["pdf_file"].help_text = (
                "Leave unchanged to keep the current private PDF. Uploading a replacement requires reprocessing."
            )

    def clean_publication_year(self):
        year = self.cleaned_data.get("publication_year")
        if year is not None and not 1800 <= year <= 2100:
            raise forms.ValidationError("Enter a publication year between 1800 and 2100.")
        return year

    def clean_doi(self):
        doi = (self.cleaned_data.get("doi") or "").strip()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if doi.lower().startswith(prefix):
                doi = doi[len(prefix):].strip()
                break
        return doi

    def clean_pdf_file(self):
        uploaded_file = self.cleaned_data.get("pdf_file")
        if not uploaded_file:
            if not self.instance.pk:
                raise forms.ValidationError("Select a journal article PDF.")
            return uploaded_file
        if not hasattr(uploaded_file, "content_type"):
            return uploaded_file

        inspection = inspect_journal_pdf(uploaded_file)
        uploaded_file._vetdojo_pdf_inspection = inspection
        duplicates = JournalArticleDetails.objects.filter(file_sha256=inspection.sha256)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError("This PDF has already been uploaded.", code="duplicate_pdf")
        self.pdf_inspection = inspection
        return uploaded_file


def normalize_text_encoding(value):
    text = str(value or "")
    for _ in range(2):
        try:
            repaired = text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if repaired == text:
            break
        text = repaired
    return text.strip()


class ManagementAuthenticationForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={"autofocus": True, "placeholder": "Admin username"}))
    password = forms.CharField(strip=False, widget=forms.PasswordInput(attrs={"placeholder": "Password"}))

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not (user.is_superuser or user.groups.filter(name="ADMIN").exists()):
            raise forms.ValidationError("This account does not have management access.", code="management_access")


class ManagementPasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": "Current password"}))
    new_password1 = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": "New password"}))
    new_password2 = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": "Confirm new password"}))


class KnowledgeCategoryForm(forms.ModelForm):
    class Meta:
        model = KnowledgeCategory
        fields = ("name", "description", "is_active")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Orthopaedics"}),
            "description": forms.Textarea(attrs={"rows": 3, "placeholder": "Optional internal description"}),
        }


class KnowledgeBaseSettingsForm(forms.ModelForm):
    class Meta:
        model = KnowledgeBaseSettings
        exclude = ("updated_by", "updated_at")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("chunk_overlap", 0) >= cleaned.get("chunk_size", 0):
            self.add_error("chunk_overlap", "Chunk overlap must be smaller than chunk size.")
        if not 0 <= cleaned.get("similarity_threshold", 0) <= 1:
            self.add_error("similarity_threshold", "Enter a value between 0 and 1.")
        if self.instance and self.instance.pk:
            if cleaned.get("embedding_model") != self.instance.embedding_model:
                self.add_error(
                    "embedding_model",
                    "Changing the embedding model requires a controlled bulk migration and cannot be switched here.",
                )
            if cleaned.get("embedding_dimensions") != self.instance.embedding_dimensions:
                self.add_error(
                    "embedding_dimensions",
                    "Changing embedding dimensions requires a controlled database migration.",
                )
        return cleaned


class RetrievalTestForm(forms.Form):
    question = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Enter a clinical question to inspect retrieval results."})
    )
    generate_preview = forms.BooleanField(
        required=False,
        help_text="Uses the configured chat model and incurs an additional API request.",
    )
