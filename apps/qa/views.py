import json
import logging
import re
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import FeedbackForm, QuestionForm
from .models import Answer, Feedback, Question
from .services import QAConfigurationError, answer_question, generate_ai_alternative
from .voice import (
    VoiceConfigurationError,
    VoiceSessionError,
    create_realtime_client_secret,
    user_can_test_voice,
)

LOGGER = logging.getLogger(__name__)


@login_required
def voice_dashboard(request):
    if not settings.VOICE_FEATURE_ENABLED:
        raise Http404
    if not user_can_test_voice(request.user):
        raise PermissionDenied
    return render(request, "qa/voice.html", {
        "voice_query_timeout_ms": settings.VOICE_QUERY_TIMEOUT_SECONDS * 1000,
    })


@require_POST
@login_required
def voice_session(request):
    if not settings.VOICE_FEATURE_ENABLED:
        raise Http404
    if not user_can_test_voice(request.user):
        raise PermissionDenied

    throttle_key = f"voice-session-create:{request.user.pk}"
    if not cache.add(throttle_key, True, timeout=settings.VOICE_SESSION_REQUEST_COOLDOWN_SECONDS):
        response = JsonResponse({"error": "Please wait before starting another voice session."}, status=429)
        response["Retry-After"] = str(settings.VOICE_SESSION_REQUEST_COOLDOWN_SECONDS)
        return response

    try:
        payload = create_realtime_client_secret(request.user)
    except VoiceConfigurationError:
        cache.delete(throttle_key)
        LOGGER.exception("Voice session configuration is invalid")
        return JsonResponse({"error": "Voice conversations are not configured."}, status=503)
    except VoiceSessionError:
        cache.delete(throttle_key)
        LOGGER.exception("Realtime voice session creation failed user_id=%s", request.user.pk)
        return JsonResponse({"error": "A voice session could not be started."}, status=502)

    response = JsonResponse(payload)
    response["Cache-Control"] = "no-store"
    return response


def _voice_source_payload(source):
    metadata = source.citation_metadata or {}
    return {
        "number": source.citation_order,
        "type": source.source_type,
        "title": source.source_title,
        "url": source.source_url,
        "excerpt": source.excerpt,
        "pdf_page": metadata.get("pdf_page_start"),
        "printed_page": metadata.get("printed_page_start"),
        "authors": metadata.get("authors", ""),
    }


def _voice_spoken_answer(answer):
    content = answer.structured_content or {}
    text = content.get("summary") or answer.text
    return re.sub(r"\s*\[Source\s+\d+\]", "", str(text), flags=re.IGNORECASE).strip()


@require_POST
@login_required
def voice_query(request):
    if not settings.VOICE_FEATURE_ENABLED:
        raise Http404
    if not user_can_test_voice(request.user):
        raise PermissionDenied

    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "The voice question was invalid."}, status=400)
    question_text = " ".join(str(data.get("question", "")).split())
    if not 3 <= len(question_text) <= 2000:
        return JsonResponse({"error": "Please ask a question between 3 and 2,000 characters."}, status=400)

    call_id = str(data.get("call_id", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", call_id):
        return JsonResponse({"error": "The voice request identifier was invalid."}, status=400)
    result_key = f"voice-query-result:{request.user.pk}:{call_id}"
    lock_key = f"voice-query-lock:{request.user.pk}:{call_id}"
    cached_result = cache.get(result_key)
    if cached_result:
        response = JsonResponse(cached_result)
        response["Cache-Control"] = "no-store"
        return response
    if not cache.add(lock_key, True, timeout=settings.VOICE_QUERY_TIMEOUT_SECONDS + 30):
        return JsonResponse({"error": "This voice question is already being processed."}, status=409)

    try:
        question = Question.objects.create(user=request.user, text=question_text)
        answer = answer_question(question)
    except Exception:
        question_id = getattr(locals().get("question"), "pk", None)
        LOGGER.exception("Voice question processing failed question_id=%s", question_id)
        return JsonResponse(
            {"error": "VetDojo could not answer that question right now.", "question_id": question_id},
            status=502,
        )
    finally:
        cache.delete(lock_key)

    content = answer.structured_content or {}
    result = {
        "question_id": question.pk,
        "answer_id": answer.pk,
        "spoken_answer": _voice_spoken_answer(answer),
        "summary": content.get("summary", answer.text),
        "sections": content.get("sections", []),
        "limitations": answer.display_limitations,
        "source_type": answer.source_type,
        "sources": [_voice_source_payload(source) for source in answer.sources.all()],
        "detail_url": reverse("qa:detail", args=[question.pk]),
    }
    cache.set(result_key, result, timeout=3600)
    response = JsonResponse(result)
    response["Cache-Control"] = "no-store"
    return response


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
