import os
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}

def database_config():
    if env_bool("USE_SQLITE", False):
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}
    url = os.getenv("DATABASE_URL", os.getenv("SUPABASE_DB_URL", "")).strip()
    if not url:
        raise RuntimeError("DATABASE_URL is required when USE_SQLITE is false.")
    parsed = urlparse(url)
    options = dict(parse_qsl(parsed.query))
    return {
        "ENGINE": "django.db.backends.postgresql", "NAME": unquote(parsed.path.lstrip("/")) or "postgres",
        "USER": unquote(parsed.username or ""), "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "", "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "60")),
        "OPTIONS": {"sslmode": options.get("sslmode", "require")},
    }

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-development-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [x.strip() for x in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if x.strip()]
CSRF_TRUSTED_ORIGINS = [x.strip() for x in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if x.strip()]
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
    "django.contrib.messages", "django.contrib.staticfiles", "apps.accounts", "apps.core", "apps.qa", "apps.management",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {"default": database_config()}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "qa:dashboard"
LOGOUT_REDIRECT_URL = "core:home"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
PRIVATE_MEDIA_ROOT = Path(os.getenv("PRIVATE_MEDIA_ROOT", BASE_DIR / "private_media"))
JOURNAL_PDF_MAX_BYTES = int(os.getenv("JOURNAL_PDF_MAX_BYTES", str(25 * 1024 * 1024)))
JOURNAL_PDF_MAX_PAGES = int(os.getenv("JOURNAL_PDF_MAX_PAGES", "500"))
JOURNAL_PDF_MIN_TEXT_CHARS = int(os.getenv("JOURNAL_PDF_MIN_TEXT_CHARS", "200"))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
OPENAI_EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4.1-mini")
OPENAI_PDF_MODEL = os.getenv("OPENAI_PDF_MODEL", OPENAI_CHAT_MODEL)
OPENAI_PDF_ANALYSIS_ENABLED = env_bool("OPENAI_PDF_ANALYSIS_ENABLED", True)
AI_PROMPT_VERSION = os.getenv("AI_PROMPT_VERSION", "v2")
AI_CACHE_TTL_DAYS = int(os.getenv("AI_CACHE_TTL_DAYS", "30"))
VOICE_FEATURE_ENABLED = env_bool("VOICE_FEATURE_ENABLED", False)
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime")
OPENAI_REALTIME_VOICE = os.getenv("OPENAI_REALTIME_VOICE", "marin")
OPENAI_REALTIME_TRANSCRIPTION_MODEL = os.getenv("OPENAI_REALTIME_TRANSCRIPTION_MODEL", "gpt-transcribe")
VOICE_CLIENT_SECRET_TTL_SECONDS = int(os.getenv("VOICE_CLIENT_SECRET_TTL_SECONDS", "60"))
VOICE_SESSION_REQUEST_COOLDOWN_SECONDS = int(os.getenv("VOICE_SESSION_REQUEST_COOLDOWN_SECONDS", "5"))
VOICE_MAX_SESSION_MINUTES = int(os.getenv("VOICE_MAX_SESSION_MINUTES", "20"))
VOICE_QUERY_TIMEOUT_SECONDS = int(os.getenv("VOICE_QUERY_TIMEOUT_SECONDS", "120"))
VOICE_VAD_THRESHOLD = float(os.getenv("VOICE_VAD_THRESHOLD", "0.55"))
VOICE_VAD_PREFIX_PADDING_MS = int(os.getenv("VOICE_VAD_PREFIX_PADDING_MS", "300"))
VOICE_VAD_SILENCE_DURATION_MS = int(os.getenv("VOICE_VAD_SILENCE_DURATION_MS", "700"))
VOICE_TEST_USERS = {
    value.strip().casefold()
    for value in os.getenv("VOICE_TEST_USERS", "").split(",")
    if value.strip()
}
RAG_CANDIDATE_COUNT = int(os.getenv("RAG_CANDIDATE_COUNT", "30"))
RAG_LEXICAL_COUNT = int(os.getenv("RAG_LEXICAL_COUNT", "40"))
RAG_PRIMARY_COUNT = int(os.getenv("RAG_PRIMARY_COUNT", "5"))
RAG_CONTEXT_COUNT = int(os.getenv("RAG_CONTEXT_COUNT", "10"))
RAG_MAX_CHUNKS_PER_SOURCE = int(os.getenv("RAG_MAX_CHUNKS_PER_SOURCE", "3"))
RAG_MIN_SIMILARITY = float(os.getenv("RAG_MIN_SIMILARITY", "0.42"))
RAG_MIN_RELEVANCE = float(os.getenv("RAG_MIN_RELEVANCE", "0.48"))
RAG_MAX_CONTEXT_CHARS = int(os.getenv("RAG_MAX_CONTEXT_CHARS", "24000"))
RAG_CHUNK_SIZE = int(os.getenv("CHUNK_SIZE_TOKENS", "900"))
RAG_CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP_TOKENS", "120"))
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT", "1800"))
CELERY_TASK_SOFT_TIME_LIMIT = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "1740"))
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_EAGER_PROPAGATES = DEBUG
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "VetDojo <noreply@example.com>")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", "0" if DEBUG else "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", not DEBUG)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", not DEBUG)
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
LOGGING = {"version": 1, "disable_existing_loggers": False, "handlers": {"console": {"class": "logging.StreamHandler"}}, "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")}}
