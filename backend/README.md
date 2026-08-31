# AI Chat API

A production-quality **FastAPI backend for the AI girlfriend/chat application**, built
around the **existing Oracle database schema** (9 tables). The schema is the source of
truth — the application adapts to it, never the other way around.

Key features:

- Account registration/login with **Argon2id** password hashing and signed **JWT** tokens
- AI **characters (personas)** served from the database, with persona system prompts
- Conversations + messages with **server-side ownership enforcement** on every resource
- **True incremental streaming** of AI replies over **Server-Sent Events (SSE)**,
  with the completed response (plus token/latency telemetry) persisted to the DB
- **Provider-agnostic AI layer**: OpenAI-compatible (covers DeepSeek), Anthropic, and a
  deterministic mock provider; provider/model/base-URL/API-key resolved **from the DB**
  per conversation
- Long-term **memories** per user/character, injected into the AI context
- Soft deletion, structured logging, centralized error handling, explicit CORS

---

## Architecture

```text
                    Client (browser / app)
                            │  HTTPS + JWT Bearer
                            ▼
                    ┌──────────────────┐
                    │      FastAPI     │
                    │  (async, uvicorn)│
                    └────────┬─────────┘
              ┌──────────────┼──────────────────┐
              ▼              ▼                  ▼
      ┌──────────────┐ ┌───────────────┐ ┌──────────────┐
      │  Auth        │ │ Conversation/ │ │   AI Service │
      │  (JWT,       │ │ Message       │ │  (context +  │
      │  Argon2id)   │ │ Services      │ │  streaming)  │
      └──────────────┘ └───────┬───────┘ └──────┬───────┘
                               │                │
              repositories / SQLAlchemy 2.x     │ provider abstraction
              (async oracledb, thin mode)       │ (OpenAI-compat / Anthropic / mock)
                               ▼                ▼
                      ┌────────────────┐   ┌──────────────┐
                      │  Oracle DB     │   │  AI Provider │
                      │  (9 tables,    │   │  (DeepSeek   │
                      │   untouched)   │   │   etc.)      │
                      └────────────────┘   └──────────────┘
```

Application layers (strict separation of concerns):

| Layer | Location | Responsibility |
|---|---|---|
| HTTP routes | `app/api/routes/` | HTTP concerns only: status codes, SSE framing, docs |
| Dependencies | `app/api/dependencies.py` | Auth extraction, settings, session factories, services |
| Services | `app/services/` | Business logic (auth, conversations, streaming flow) |
| Repositories | `app/db/repositories/` | SQL only; ownership filtering lives here |
| ORM models | `app/db/models/` | Map the existing Oracle schema (SQLAlchemy 2.x typed) |
| Schemas | `app/schemas/` | Pydantic v2 request/response contracts |
| AI layer | `app/ai/` | Provider protocol, context builder, provider implementations |
| Core | `app/core/` | Configuration, security, logging, time helpers |

The AI streaming flow is deliberately **session-lean**: the user message is persisted in
its own short transaction, history/context are loaded in a second, and the completed AI
reply (+ `MESSAGE_GENERATIONS` telemetry) in a third — no database connection is held
open while the provider streams.

## Technology stack

- **Python 3.13+** (project is developed/tested on 3.13; 3.14 works too)
- **FastAPI** (modern lifespan-based startup, async endpoints)
- **Pydantic v2** (+ `pydantic-settings`) for validation and configuration
- **SQLAlchemy 2.x** async (2.0-style typed mappings) + **python-oracledb** thin mode
  (no Oracle client libraries required)
- **argon2-cffi** (Argon2id password hashing), **PyJWT** (signed access tokens)
- **httpx** (async provider HTTP), **uvicorn** (ASGI server)
- **uv** for dependency/environment management
- **ruff** (lint + format), **mypy** (strict), **pytest** (+ pytest-asyncio)

## Project structure

```text
app/
  main.py                 # app assembly, lifespan, middleware, error handlers
  api/                    # dependencies + routers (auth, users, characters,
                          #   ai/models, conversations, messages, memories)
  core/                   # config.py, security.py, logging.py, time.py
  db/
    database.py           # async engine + session factory (oracledb thin)
    models/               # SQLAlchemy models for all 9 Oracle tables
    repositories/         # per-entity data access
  schemas/                # Pydantic v2 input/output contracts (+ SSE payloads)
  services/               # auth, user, conversation, message, ai services
  ai/
    base.py               # AIProvider protocol + provider-independent types
    context.py            # persona + memories + budgeted history → ChatRequest
    registry.py           # provider factory
    providers/            # openai (OpenAI-compatible incl. DeepSeek),
                          #   anthropic, mock
  exceptions/             # AppError hierarchy → HTTP status codes
tests/                    # 41 tests: auth, authorization, CRUD, streaming, context
docs/database.md          # discovered Oracle schema documentation
scripts/                  # SQL inspection scripts used for discovery
pyproject.toml            # dependencies + ruff/mypy/pytest configuration
.env.example              # configuration template (no real secrets)
```

## Database configuration

The API connects to the existing Oracle database; **it never creates, drops, truncates,
or alters tables** — with one exception: the reply feature needs a nullable
`MESSAGES.REPLY_TO_ID` column (self-referential FK). Run the one-off migration once
before first use:

```bash
sqlplus -S chatbot/chatbot@192.168.1.42:1521/pdb.oracle.ek @scripts/migrate_reply_to.sql
```

See `docs/database.md` for the full discovered schema.

- `DATABASE_URL` follows SQLAlchemy URL syntax (thin mode, no Oracle client needed):

  ```env
  DATABASE_URL=oracle+oracledb://chatbot:chatbot@192.168.1.42:1521/?service_name=pdb.oracle.ek
  ```

- The database is the **source of AI provider configuration**: `PROVIDERS` →
  `AI_MODELS` → `AI_ENDPOINTS` (base URL + API key). Each conversation is pinned to a
  model; at generation time the app resolves provider, endpoint, API key, and model name
  from these tables. API keys stored in `AI_ENDPOINTS.API_KEY` are never returned by the
  API or written to logs.
- New conversations resolve their model as: explicit request value → user's
  `DEFAULT_MODEL_ID` (if active) → first active model.
- `DELETE /conversations/{id}` performs a **soft delete** (`STATUS='DELETED'`); rows and
  messages are preserved.

## Environment variables

Copy `.env.example` to `.env` and fill in real values (never commit `.env`):

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | Environment name (for logs) |
| `DATABASE_URL` | Oracle PDB URL | SQLAlchemy database URL |
| `JWT_SECRET` | *(placeholder)* | HMAC key for signing tokens — **must be overridden** |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Access token lifetime |
| `CORS_ORIGINS` | localhost dev origins | Comma-separated allowed origins |
| `AI_PROVIDER` | `database` | `database` \| `mock` \| `openai` \| `anthropic` |
| `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL` | *(empty)* | Fallback credentials, used only when `AI_PROVIDER != database` |
| `AI_CONTEXT_MAX_MESSAGES` | `50` | History window cap |
| `AI_DEFAULT_CONTEXT_CHARS` | `16000` | Default token budget ≈ chars/4 |
| `AI_TEMPERATURE` / `AI_MAX_TOKENS` | `0.8` / `1024` | Generation parameters |
| `AI_STREAM_TIMEOUT_SECONDS` | `120` | Provider read timeout |

## Installation & running

```bash
# 1. Install dependencies into a managed virtualenv (Python 3.13+)
uv sync

# 2. Configure
cp .env.example .env   # then edit .env (JWT_SECRET at minimum)

# 3. Run the API
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Interactive docs: `http://localhost:8000/docs` (Swagger UI) and `/redoc`.
FastAPI also serves the OpenAPI schema at `/openapi.json`.

Run tests / checks:

```bash
uv run pytest            # 41 tests — SQLite in-memory, no external services needed
uv run ruff check app tests
uv run ruff format --check app tests
uv run mypy app
```

## Authentication

- `POST /auth/register` — creates an account. The password is hashed with **Argon2id**
  (argon2-cffi) and only the hash is stored in `USERS.PASSWORD_HASH`. Usernames are
  unique (DB-enforced).
- `POST /auth/login` — verifies the password (timing-hardened against unknown
  usernames), updates `LAST_LOGIN_AT`, and returns a signed **JWT** access token
  (`sub` = user id, `iat`/`exp` validated on every request). After 5 failed attempts
  the username is throttled for 60 s (`429` + `Retry-After`) — in-memory; use a shared
  store (e.g. Redis) in multi-worker deployments.
- Every endpoint except register/login requires `Authorization: Bearer <token>`.
  Invalid/expired tokens → `401`; disabled accounts → `403`.
- **Authorization is enforced server-side on every request**: conversations, messages,
  and memories are always filtered by the authenticated user; a foreign conversation id
  yields `404`, indistinguishable from a nonexistent one.
- **Characters are built-in or private**: a character created via `POST /characters` is
  owned by its creator (`CHARACTERS.OWNER_USER_ID`) and visible only to them; built-in
  characters (`OWNER_USER_ID IS NULL`) are visible to everyone. The owner always comes
  from the access token, never from the request body.
- **Administrators** (`USERS.ROLE = 'ROLE_admin'`, set only in the database) get full CRUD
  over characters and models through the same paths — see *Roles* below.

## API overview

| Method & path | Description |
|---|---|
| `POST /auth/register` | Create account (public) |
| `POST /auth/login` | Get JWT access token (public) |
| `GET /me` · `PATCH /me` | Profile; update display name / default model |
| `GET /characters` · `GET /characters/{id}` | Active AI characters: built-in + the user's own (admin: all) |
| `POST /characters` | Create a private character owned by the caller |
| `PATCH /characters/{id}` · `DELETE` | Edit / soft-delete your own character (admin: any) |
| `GET /models` · `GET /models/{id}` | Active AI models (admin: inactive ones too) |
| `POST /models` · `PATCH` · `DELETE` | Manage the model catalog — **`ROLE_admin` only** |
| `GET /personas` · `POST /personas` | The user's personas (who *they* role-play as) |
| `GET /personas/{id}` · `PATCH` · `DELETE` | Get / update / delete — private to the owner |
| `GET /conversations` · `POST /conversations` | List (limit/offset) / create (optional `user_persona_id`) |
| `GET /conversations/{id}` · `PATCH` · `DELETE` | Get / rename / soft delete |
| `GET /conversations/{id}/messages` | History (`limit`, `before_id` cursor) |
| `POST /conversations/{id}/messages` | Send message → **streamed AI reply (SSE)**; optional `reply_to_id` references an earlier message in the same conversation (404 otherwise) |
| `GET /memories?character_id=` | The user's long-term memories |

All responses use Pydantic models; errors are consistently `{"detail": "..."}` with
proper status codes (400/401/403/404/409/422/429/500/503).

## Roles

`USERS.ROLE` is either `ROLE_user` (the database default) or `ROLE_admin`. **No endpoint
writes `ROLE`** — an administrator is created with a direct database update:

```sql
UPDATE CHATBOT.USERS SET ROLE = 'ROLE_admin' WHERE USERNAME = 'someone';
```

Authorization always reads the role from the user's row on each request, so a promotion or
demotion takes effect on the next call with an existing token. The comparison is exact and
case-sensitive. Admin-only routes use the `require_admin` dependency
(`app/api/dependencies.py`): no token → `401`, authenticated non-admin → `403`.

| | Regular user | `ROLE_admin` |
|---|---|---|
| List / read characters | Active built-in + own | Every character, any owner or status |
| Create character | Owned by self | Any owner, incl. global (`owner_user_id: null`) |
| Update / delete character | Own characters only | Any character |
| `owner_user_id`, `status` in a character body | `403` | Allowed |
| List / read models | Active only | Including inactive |
| Create / update / delete models | `403` | Allowed |

Deletion is soft on both resources — characters get `STATUS='DELETED'`, models get
`ACTIVE=0` — so conversations, memories and telemetry keep their foreign keys.

## AI streaming

`POST /conversations/{id}/messages` responds with `Content-Type: text/event-stream`:

```text
event: message.created          → {"conversation_id":…, "message": {…user message…}}
event: message.delta            → {"content": "Hello"}
event: message.delta            → {"content": ", I"}
…                               (one event per provider chunk, flushed immediately)
event: message.completed        → {"message": {…assistant message…},
                                   "usage": {"input_tokens":…, "output_tokens":…, "total_tokens":…},
                                   "latency_ms": …}
event: error                    → {"code": "provider_error"|"stream_interrupted", "detail": …}
```

Guarantees:

- **Real streaming** — the provider's async stream is consumed incrementally and each
  chunk is forwarded to the client as it arrives (never generated whole then chopped).
- The user message is **persisted before** streaming starts, so history is complete even
  if generation fails; the completed assistant reply (and a `MESSAGE_GENERATIONS`
  telemetry row: tokens, latency, temperature, purpose=`chat`) is persisted only after
  the provider finishes.
- **Client disconnects** cancel the provider stream (resources released via `aclose()`)
  and persist nothing partial.
- **Provider failures** are delivered as an `error` event; pre-flight failures
  (e.g. conversation not owned) are regular HTTP errors because they occur before the
  response starts.
- No database session is held open across the stream.

### Conversation context

For each generation the app builds the model context from persisted data
(`app/ai/context.py`, pure functions):

1. **Character**: `CHARACTERS.SYSTEM_PROMPT` is used as the system prompt (fallback: a
   built-in warm-companion prompt).
2. **Memories**: the user's top active memories for that character (by importance)
   are appended to the system prompt — long-term memory support, ready for retrieval
   ranking.
3. **User persona** (optional): when the conversation has a `USER_PERSONA_ID`, the
   persona's provided fields are appended to the *same* system message inside a fenced
   `[USER PERSONA] … [END USER PERSONA]` block, introduced as background data that
   carries no authority. Field values are flattened to one line and the block markers
   are stripped from them, so persona text cannot close the block or impersonate
   application instructions — the character prompt above stays authoritative. With no
   persona the system prompt is byte-for-byte what it was before the feature, and the
   persona row is not even queried.
4. **History**: the most recent messages that fit a token budget
   (`context_window × 4` chars if the model row has one, else `AI_DEFAULT_CONTEXT_CHARS`),
   capped at `AI_CONTEXT_MAX_MESSAGES`; the newest message is never dropped. The walk is
   backwards/truncation-friendly, so summarization or smarter budgeting can be added
   without touching the rest of the service.

### Configuring the AI provider

- **Default (`AI_PROVIDER=database`)**: provider/model/endpoint/API key come from the
  Oracle tables per conversation. The seeded `DeepSeek` row is OpenAI-compatible and
  works out of the box. Unknown provider names fall back to the OpenAI-compatible
  implementation (suitable for custom gateways).
- `AI_PROVIDER=openai` / `anthropic` / `mock`: bypass the DB resolution and use the
  `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL` env values (`mock` needs nothing and is
  great for local frontend development).

## Security notes

- Passwords: Argon2id only; plaintext never stored or logged.
- Tokens: signed JWTs, algorithm pinned, expiry enforced, minimal claims (`sub` only).
- Secrets: `JWT_SECRET` and provider API keys come from environment/DB config only.
- Logs never contain passwords, tokens, API keys, or message contents (chat messages
  may contain private user data).
- SQL is always parameterized (SQLAlchemy ORM).
- CORS is explicit (allowlist from `CORS_ORIGINS`), credentials enabled.
- Provider/DB failures are logged server-side and returned to clients generically —
  no stack traces, connection strings, or internal details leak.
