import hashlib

from django.conf import settings
from openai import OpenAI, OpenAIError


class VoiceConfigurationError(RuntimeError):
    pass


class VoiceSessionError(RuntimeError):
    pass


def user_can_test_voice(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.groups.filter(name="ADMIN").exists():
        return True
    identifiers = {user.username.casefold(), user.email.casefold()}
    return bool(identifiers & settings.VOICE_TEST_USERS)


def safety_identifier(user):
    value = f"{settings.SECRET_KEY}:voice-user:{user.pk}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validated_ttl():
    ttl = settings.VOICE_CLIENT_SECRET_TTL_SECONDS
    if not 10 <= ttl <= 7200:
        raise VoiceConfigurationError("VOICE_CLIENT_SECRET_TTL_SECONDS must be between 10 and 7200.")
    return ttl


def _turn_detection_config():
    threshold = settings.VOICE_VAD_THRESHOLD
    prefix_padding = settings.VOICE_VAD_PREFIX_PADDING_MS
    silence_duration = settings.VOICE_VAD_SILENCE_DURATION_MS
    if not 0 <= threshold <= 1:
        raise VoiceConfigurationError("VOICE_VAD_THRESHOLD must be between 0 and 1.")
    if prefix_padding < 0:
        raise VoiceConfigurationError("VOICE_VAD_PREFIX_PADDING_MS cannot be negative.")
    if silence_duration <= 0:
        raise VoiceConfigurationError("VOICE_VAD_SILENCE_DURATION_MS must be positive.")
    return {
        "type": "server_vad",
        "threshold": threshold,
        "prefix_padding_ms": prefix_padding,
        "silence_duration_ms": silence_duration,
        "create_response": True,
        "interrupt_response": True,
    }


def create_realtime_client_secret(user):
    if not settings.OPENAI_API_KEY:
        raise VoiceConfigurationError("OPENAI_API_KEY is not configured.")
    if not settings.OPENAI_REALTIME_MODEL:
        raise VoiceConfigurationError("OPENAI_REALTIME_MODEL is not configured.")
    if not settings.OPENAI_REALTIME_VOICE:
        raise VoiceConfigurationError("OPENAI_REALTIME_VOICE is not configured.")
    if not settings.OPENAI_REALTIME_TRANSCRIPTION_MODEL:
        raise VoiceConfigurationError("OPENAI_REALTIME_TRANSCRIPTION_MODEL is not configured.")

    client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=20.0, max_retries=1)
    try:
        secret = client.realtime.client_secrets.create(
            expires_after={"anchor": "created_at", "seconds": _validated_ttl()},
            session={
                "type": "realtime",
                "model": settings.OPENAI_REALTIME_MODEL,
                "output_modalities": ["audio"],
                "audio": {
                    "input": {
                        "noise_reduction": {"type": "far_field"},
                        "transcription": {
                            "model": settings.OPENAI_REALTIME_TRANSCRIPTION_MODEL,
                            "language": "en",
                        },
                        "turn_detection": _turn_detection_config(),
                    },
                    "output": {"voice": settings.OPENAI_REALTIME_VOICE},
                },
                "tools": [
                    {
                        "type": "function",
                        "name": "search_vetdojo_knowledge",
                        "description": (
                            "Search VetDojo's approved veterinary knowledge base and save the "
                            "question and answer. You must use this tool for every user question."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "question": {
                                    "type": "string",
                                    "description": "The user's complete veterinary question.",
                                }
                            },
                            "required": ["question"],
                            "additionalProperties": False,
                        },
                    }
                ],
                "tool_choice": "required",
                "instructions": (
                    "You are VetDojo's hands-free veterinary education assistant. For every user "
                    "question, call search_vetdojo_knowledge and do not answer from your own knowledge. "
                    "After the tool returns, speak its spoken_answer faithfully and concisely. Do not "
                    "read references, URLs, citation markers, or technical metadata aloud because they "
                    "are displayed on screen. If the tool reports an error, briefly ask the user to try again."
                ),
            },
            extra_headers={"OpenAI-Safety-Identifier": safety_identifier(user)},
        )
    except OpenAIError as exc:
        raise VoiceSessionError("OpenAI could not create a Realtime session.") from exc

    if not secret.value:
        raise VoiceSessionError("OpenAI returned an invalid Realtime client secret.")
    return {
        "client_secret": secret.value,
        "expires_at": secret.expires_at,
        "model": settings.OPENAI_REALTIME_MODEL,
        "voice": settings.OPENAI_REALTIME_VOICE,
        "max_session_minutes": settings.VOICE_MAX_SESSION_MINUTES,
    }
