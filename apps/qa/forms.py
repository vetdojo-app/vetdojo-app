from django import forms

class QuestionForm(forms.Form):
    question = forms.CharField(
        min_length=5, max_length=2000,
        widget=forms.Textarea(attrs={"rows": 5, "placeholder": "Ask a clinical question, for example: What complications should I consider after TPLO surgery?", "autofocus": True}),
    )

    def clean_question(self):
        return " ".join(self.cleaned_data["question"].split())

class FeedbackForm(forms.Form):
    helpful = forms.BooleanField(required=False)
    comment = forms.CharField(max_length=1000, required=False)
