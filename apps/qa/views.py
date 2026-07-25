import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import FeedbackForm, QuestionForm
from .models import Answer, Feedback, Question
from .services import QAConfigurationError, answer_question, generate_ai_alternative

LOGGER = logging.getLogger(__name__)


@login_required
def dashboard(request):
    form = QuestionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        question = Question.objects.create(user=request.user, text=form.cleaned_data["question"])
        try:
            answer_question(question)
        except QAConfigurationError as exc:
            messages.error(request, f"The question service is not configured: {exc}")
        except Exception:
            messages.error(request, "We could not generate an answer right now. Please try again shortly.")
        return redirect("qa:detail", pk=question.pk)
    recent = request.user.questions.prefetch_related("answers")[:6]
    return render(request, "qa/dashboard.html", {"form": form, "recent_questions": recent})


@login_required
def detail(request, pk):
    question = get_object_or_404(Question.objects.prefetch_related("answers__sources"), pk=pk, user=request.user)
    return render(request, "qa/detail.html", {"question": question})


@login_required
def history(request):
    questions = request.user.questions.prefetch_related("answers")
    return render(request, "qa/history.html", {"questions": questions})


@require_POST
@login_required
def ai_alternative(request, pk):
    question = get_object_or_404(Question, pk=pk, user=request.user)
    if not question.answers.filter(source_type=Answer.SourceType.CLIENT).exists():
        return HttpResponseBadRequest("An AI alternative is only available after a library-based answer.")
    try:
        generate_ai_alternative(question)
        messages.success(request, "A separate general AI answer has been generated.")
    except Exception:
        LOGGER.exception("AI alternative failed question_id=%s", question.pk)
        messages.error(request, "The AI alternative could not be generated right now.")
    return redirect("qa:detail", pk=pk)


@require_POST
@login_required
def feedback(request, answer_id):
    answer = get_object_or_404(Answer, pk=answer_id, question__user=request.user)
    form = FeedbackForm(request.POST)
    if not form.is_valid() or request.POST.get("rating") not in {"helpful", "not_helpful"}:
        return HttpResponseBadRequest("Invalid feedback.")
    Feedback.objects.update_or_create(
        user=request.user, answer=answer,
        defaults={"helpful": request.POST["rating"] == "helpful", "comment": form.cleaned_data["comment"]},
    )
    messages.success(request, "Thank you — your feedback helps improve the platform.")
    return redirect("qa:detail", pk=answer.question_id)
