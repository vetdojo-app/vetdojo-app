# VetDojo

Django web application for authenticated, source-aware veterinary clinical Q&A. It searches the VetDojo knowledge base first, creates a grounded answer with source citations when evidence is sufficient, and clearly labels general-AI fallback answers.

## Features

- Custom Django user accounts, login, logout, and password reset
- Professional responsive Django-template interface
- Read-only Django mappings to `rag_source_documents` and `rag_chunks`
- pgvector retrieval through the existing `match_rag_chunks` function
- Configurable relevance threshold, context limits, and overlap deduplication
- Structured library-based answers with citations to transcripts, consultations, and journal articles
- Private journal PDF upload, validation, extraction, metadata review, page-aware chunking, and retrieval
- Automatic general-AI response when library evidence is insufficient
- Optional separate AI alternative after a library-based answer
- Versioned exact-question AI fallback cache (library search always runs first)
- Question history, source audit records, feedback, admin, tests, and Docker setup

## Important security step

Rotate the database password that was previously stored in the workspace `important.txt` file before using this application. Do not reuse that credential. The file is now ignored, but ignoring it does not remove it from Git history or make an exposed credential safe.

## Local setup with the real Supabase database

From the workspace root:

```powershell
cd web_app
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`:

```env
DJANGO_SECRET_KEY=a-long-random-secret
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
USE_SQLITE=false
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/postgres?sslmode=require
OPENAI_API_KEY=your-openai-api-key
```

Use the same Supabase project/database as Phase 2. The database must already contain `rag_source_documents`, `rag_chunks`, pgvector, and `match_rag_chunks`.

Then run:

```powershell
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open `http://127.0.0.1:8000/`, create a normal user account, and ask a question.

`migrate` creates Django authentication, session, question, answer, citation, and feedback tables in the same database. It does not modify the Phase 2 tables because their Django models use `managed = False`.

## UI-only local test

To inspect account pages and the design before connecting Supabase, omit `DATABASE_URL` and set:

```env
USE_SQLITE=true
```

Run `python manage.py migrate` and `python manage.py runserver`. Authentication and static pages work, but real questions cannot use Phase 2 retrieval because SQLite does not contain pgvector or `match_rag_chunks`.

## Retrieval settings

| Variable | Default | Purpose |
|---|---:|---|
| `RAG_CANDIDATE_COUNT` | 30 | Chunks returned by vector search |
| `RAG_LEXICAL_COUNT` | 40 | Additional title/content keyword candidates |
| `RAG_PRIMARY_COUNT` | 5 | Strong matches used for neighbor expansion |
| `RAG_CONTEXT_COUNT` | 10 | Maximum chunks provided to the chat model |
| `RAG_MAX_CHUNKS_PER_SOURCE` | 3 | Context diversity limit per video |
| `RAG_MIN_RELEVANCE` | 0.48 | Combined semantic/lexical evidence cutoff |
| `RAG_MIN_SIMILARITY` | 0.42 | Retained for compatibility and diagnostics |
| `RAG_MAX_CONTEXT_CHARS` | 24000 | Context safety limit |
| `OPENAI_CHAT_MODEL` | `gpt-4.1-mini` | Answer-generation model |

The similarity threshold is an initial value, not a clinical confidence score. Review it using a specialist-approved evaluation set before production launch.

## Verification

```powershell
python manage.py check
python manage.py test
python manage.py collectstatic --noinput
```

Health endpoint: `GET /health/`

## Answer behavior

1. Embed the question using the Phase 2 embedding model and dimensions.
2. Call `match_rag_chunks` and remove candidates below the configured threshold.
3. Deduplicate adjacent overlapping chunks and enforce context limits.
4. If evidence exists, generate only from that evidence and show referenced videos.
5. If no evidence exists, generate a visibly labeled general-AI response.
6. For a library answer, allow the user to request one separate general-AI alternative.

General-AI answers never display unrelated videos, and the original library answer is retained.

Automatic AI fallback answers are cached for `AI_CACHE_TTL_DAYS` (30 by default). A later exact normalized question still searches the VetDojo library first, then reuses a compatible cache entry only when the library remains insufficient. Cache entries are separated by model and prompt version.

## Production notes

- Set `DJANGO_DEBUG=false` and use a strong `DJANGO_SECRET_KEY`.
- Set production hosts and `CSRF_TRUSTED_ORIGINS` with HTTPS URLs.
- Configure a real SMTP email backend for password resets.
- Use a restricted PostgreSQL role, managed secrets, HTTPS, backups, monitoring, and rate limiting at the proxy/application boundary.
- Run `python manage.py collectstatic --noinput` during deployment.
- Gunicorn in the included Dockerfile is intended for Linux containers, not direct Windows execution.
