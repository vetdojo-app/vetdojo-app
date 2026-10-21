import hashlib
from functools import wraps
from urllib.parse import urlencode

from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

from .models import AIDisclaimerAcceptance


DISCLAIMER_VERSION = "2026-10-19"
DISCLAIMER_TEXT = """VetDojo AI Surgical Avatar — Disclaimer and User Acknowledgement
Educational use by veterinary professionals
The VetDojo AI Surgical Avatar is an artificial intelligence educational tool provided by Charles Kuntz Veterinary Consulting, trading as VetDojo (ABN 86 657 032 025). It is intended for veterinary professionals seeking general information and continuing education.
You are interacting with AI, not Dr Charles Kuntz
The platform uses a large language model informed by educational materials, including transcripts of Dr Charles Kuntz’s YouTube videos and selected PDF documents. Questions may be submitted using text or voice recognition, and responses may be delivered as text or synthesised speech.
The voice and conversational style do not mean that Dr Kuntz is personally answering your question. Responses are generated automatically and should not be understood as having been individually reviewed, approved or endorsed by him.
AI responses can be incorrect
The system may produce inaccurate, incomplete, outdated, misleading or fabricated information, including incorrect references. It may misinterpret questions, omit important qualifications or combine material from different clinical contexts. A confident or convincing response does not establish accuracy.
Source materials may describe techniques or opinions applicable to particular cases or circumstances. Their inclusion does not mean that an AI-generated response accurately reproduces those materials or is suitable for your patient.
Voice recognition and spoken responses
Voice recognition may incorrectly interpret clinical terminology, drug names, numbers, units, anatomical structures or other important details. Synthesised speech may also mispronounce or ambiguously communicate information.
Check that your question has been understood correctly. Independently verify clinically significant details, especially drug doses, concentrations, routes, measurements and procedural instructions. If text and spoken responses differ or are unclear, do not act on either until the information has been independently confirmed.
Not an individual consultation or operative supervisor
The platform provides general educational information. It does not examine patients, establish a veterinarian–client–patient relationship, provide a specialist consultation or assume responsibility for patient management.
Responses may not apply to an individual patient, even when you supply a detailed case history. The platform must not be relied on as the sole basis for diagnosis, prescribing, treatment selection, surgical planning or intraoperative decisions. It is not an emergency service or a substitute for direct clinical supervision, practical training or timely specialist assistance.
Your professional responsibilities
You remain responsible for your clinical decisions, patient care, informed consent and compliance with applicable professional and legal requirements.
Before applying information, assess its relevance to the patient and independently verify it against appropriate current references, product information and clinical evidence. Work within your training, competence and available facilities, and obtain direct professional advice or referral when appropriate.
Privacy and confidential information
Do not submit client names, contact details, identifying patient records or other personal or confidential information. De-identify case descriptions and ensure you are authorised to submit any material you provide.
Voice input requires processing of your audio to interpret your question. Review the platform’s Privacy Policy and voice-data notice before enabling the microphone or submitting information. Avoid capturing conversations or voices of other people without appropriate permission.
Liability and statutory rights
You acknowledge the limitations of AI-generated information and the risks of applying it without independent verification.
To the extent permitted by law, VetDojo and Dr Charles Kuntz exclude liability for loss, damage or injury arising from reliance on inaccurate, incomplete or inappropriate AI-generated responses.
Nothing in this disclaimer excludes, restricts or modifies any consumer guarantee, right, remedy or liability that cannot lawfully be excluded, restricted or modified, including under the Australian Consumer Law.
Contact
To report an inaccurate response or raise a concern, contact admin@vetdojo.com."""
DISCLAIMER_CONTENT_HASH = hashlib.sha256(DISCLAIMER_TEXT.encode("utf-8")).hexdigest()


def has_current_disclaimer_acceptance(user):
    return user.is_authenticated and AIDisclaimerAcceptance.objects.filter(
        user=user,
        disclaimer_version=DISCLAIMER_VERSION,
        content_hash=DISCLAIMER_CONTENT_HASH,
    ).exists()


def disclaimer_required(source=AIDisclaimerAcceptance.Source.TEXT, json_response=False):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if has_current_disclaimer_acceptance(request.user):
                return view_func(request, *args, **kwargs)
            if json_response:
                return JsonResponse(
                    {
                        "error": "Please accept the current AI disclaimer before using the assistant.",
                        "disclaimer_required": True,
                        "disclaimer_url": reverse("qa:disclaimer"),
                    },
                    status=403,
                )
            query = urlencode({"source": source, "next": request.get_full_path()})
            return redirect(f"{reverse('qa:disclaimer')}?{query}")

        return wrapped

    return decorator
