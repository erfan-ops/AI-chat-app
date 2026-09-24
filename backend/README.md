# AI Chat API

A production-quality **FastAPI backend for the AI chat application**, built
around the **existing Oracle database schema** (11 tables). The schema is the source of
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
                      │  (11 tables,   │   │  (DeepSeek   │
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
- **ruff** (lint + format), **ty** (type checking), **pytest** (+ pytest-asyncio)

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
tests/                    # 164 tests: auth (incl. 2FA), authorization, CRUD, streaming, context
docs/database.md          # discovered Oracle schema documentation
scripts/                  # SQL inspection scripts used for discovery
pyproject.toml            # dependencies + ruff/ty/pytest configuration
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
| `CLOUDINARY_CLOUD_NAME` / `CLOUDINARY_API_KEY` / `CLOUDINARY_API_SECRET` | *(empty)* | Cloudinary credentials for signed avatar uploads. The secret stays server-side; unset disables the feature (`POST /cloudinary/signature` → 503) |
| `SMS_IR_API_KEY` | *(empty)* | SMS.ir API key for two-step codes. Unset disables the SMS channel (`503 SMS is not configured`) |
| `SMS_IR_TEMPLATE_ID` | `601570` | SMS.ir "send verify code" template; its parameters are `USERNAME` and `CODE` |
| `SMS_IR_BASE_URL` / `SMS_IR_TIMEOUT_SECONDS` | `https://api.sms.ir` / `10` | Provider endpoint (overridable for a local stub) and request timeout |
| `RESEND_API_KEY` | *(empty)* | Resend API key for two-step codes sent by email. Unset disables the email channel (`503 Email is not configured`) |
| `RESEND_FROM_EMAIL` | `AI-chat@mail.erfancodes.ir` | Sender address; its domain must be verified in Resend |
| `RESEND_ACTIVATION_SUBJECT` | `Confirm your email address` | Subject for the code that confirms a new address. The bodies of both emails are the templates in `app/services/email_service.py` |
| `RESEND_OTP_SUBJECT` / `RESEND_TIMEOUT_SECONDS` | `Your AI Chat verification code` / `10` | Subject line and request timeout |
| `OTP_CODE_TTL_SECONDS` / `OTP_RESEND_COOLDOWN_SECONDS` | `120` / `60` | Code lifetime and minimum gap between two codes for one user (per delivery method) |
| `OTP_MAX_VERIFY_ATTEMPTS` / `OTP_MAX_SENDS_PER_DAY` | `5` / `10` | Wrong guesses allowed per code, and the daily send cap per user (across methods) |

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
uv run pytest            # 164 tests — SQLite in-memory, no external services needed
uv run ruff check app tests
uv run ruff format --check app tests
uv run ty check app
```

## Authentication

- `POST /auth/register` — creates an account. The password is hashed with **Argon2id**
  (argon2-cffi) and only the hash is stored in `USERS.PASSWORD_HASH`. Usernames are
  unique (DB-enforced, `UK_USERS_USERNAME`) and case-sensitive; `PATCH /me` can change
  one later and answers `409` if another account already holds it. Existing tokens stay
  valid across a rename — they carry the user id, not the username.
- `POST /auth/login` — verifies the password (timing-hardened against unknown
  usernames), updates `LAST_LOGIN_AT`, and returns a signed **JWT** access token
  (`sub` = user id, `iat`/`exp` validated on every request). After 5 failed attempts
  the username is throttled for 60 s (`429` + `Retry-After`) — in-memory; use a shared
  store (e.g. Redis) in multi-worker deployments.
- Every endpoint except register/login requires `Authorization: Bearer <token>`.
  Invalid/expired tokens → `401`; disabled accounts → `403`.
- `POST /me/password` replaces the password after checking the current one. A wrong one
  is `400` (a `401` would sign the caller out of the session they are asking from), and
  failures share the login throttle — otherwise a stolen token would be an unlimited
  oracle for guessing the password. Tokens already issued keep working: this codebase has
  no revocation, so the new password applies at the next sign-in.
- **Authorization is enforced server-side on every request**: conversations, messages,
  and memories are always filtered by the authenticated user; a foreign conversation id
  yields `404`, indistinguishable from a nonexistent one.
- **Characters are built-in or private**: a character created via `POST /characters` is
  owned by its creator (`CHARACTERS.OWNER_USER_ID`) and visible only to them; built-in
  characters (`OWNER_USER_ID IS NULL`) are visible to everyone. The owner always comes
  from the access token, never from the request body.
- **Administrators** (`USERS.ROLE = 'ROLE_admin'`, set only in the database) get full CRUD
  over characters and models through the same paths — see *Roles* below.

### Two-step verification (one-time codes)

A second factor is delivered as a one-time code, by **SMS** or by **email** — the same
generation, expiry, attempt and rate-limit logic either way; only the provider differs
(`app/services/otp_delivery.py` is the single place that picks one).

- **Enabling** is two calls: `POST /me/otp/enable` sends a code to a mobile number or an
  email address, and `POST /me/otp/verify` confirms it. Only then is the destination
  stored (`USERS.MOBILE_NUMBER` / `USERS.EMAIL`) and `USERS.OTP_ENABLED` set. Verifying a
  *first* contact also makes it the default method; verifying a second one later does not
  move it.
- **Changing a contact** is the same two calls with a different destination: the new one
  is verified before it replaces the old, so the current number or address keeps working
  until the new one is confirmed. A contact belongs to one account
  (`UK_USERS_MOBILE_NUMBER` / `UK_USERS_EMAIL`), so a destination another account already
  verified is refused with `409` — checked before the code is sent.
- **Logging in** with two-step on returns `otp_required` + `challenge_id`, the
  `delivery_method` used, and `alternative_method` when the other channel is usable.
  `POST /auth/login/otp/method` re-sends to that other channel **for this login only** —
  the saved default (`PATCH /me {preferred_otp_method}`) is never changed by it.
- **Privacy**: the login challenge names the channel but never the destination — someone
  holding the password must not learn the phone suffix or the address. The settings flow
  shows a masked destination (`+98 912 *** 6789`, `al***@example.com`) because the caller
  just typed it.
- **Fail closed**: if the default channel has no verified destination, login answers
  `503` rather than silently using the other one or dropping to password-only. A
  configured-but-unusable provider (no API key) is also a `503`, never a silent skip.
- Codes are 6 digits, stored only as an HMAC, single-use, valid for
  `OTP_CODE_TTL_SECONDS`, and dropped after `OTP_MAX_VERIFY_ATTEMPTS` wrong guesses.
  Rejections are **400, never 401** — the client signs out on an authenticated 401. The
  cooldown is per delivery method (so switching channels is immediate) while the daily
  send cap is per user (so switching buys no extra sends). The challenge store is
  in-process: **single worker or sticky sessions** — see `docs/otp-2fa-notes.md`.

## API overview

| Method & path | Description |
|---|---|
| `POST /auth/register` | Create account (public) |
| `POST /auth/login` | Get JWT access token (public); with two-step on, returns `otp_required` + a `challenge_id` instead |
| `POST /auth/login/otp` | Complete a two-step login with the code (public) |
| `POST /auth/login/otp/method` | Send this login's code through the other channel instead (public) |
| `GET /me` · `PATCH /me` | Profile; update username / display name / default model / `preferred_otp_method` (a taken username is `409`) |
| `POST /me/password` | Change the password — needs the current one (`400` if wrong, never `401`) |
| `POST /me/otp/enable` · `/me/otp/verify` | Verify a mobile number or email address, then turn two-step on |
| `POST /me/otp/disable` | Turn two-step off; verified contacts are kept |
| `GET /characters` | Active AI characters: built-in + the user's own — the same scope for administrators |
| `GET /characters/{id}` | One character (admin: any owner or status) |
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
| List characters | Active built-in + own | Active built-in + own (same scope) |
| Read one character | Active built-in or own | Any character, any owner or status |
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
   built-in default assistant prompt).
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
4. **The user's clock** (optional): when the client sends its timezone and UTC offset
   (the browser knows both), a `CURRENT TIME` line states what time it is where the user
   is, with the offset, and says the history timestamps are UTC. That is the only place
   the user's own clock is stated; without it the model sees UTC alone. The timezone name
   is client-supplied text, so only zone-name characters survive it.
5. **History**: the most recent messages that fit a token budget
   (`context_window × 4` chars if the model row has one, else `AI_DEFAULT_CONTEXT_CHARS`),
   capped at `AI_CONTEXT_MAX_MESSAGES`; the newest message is never dropped. The walk is
   backwards/truncation-friendly, so summarization or smarter budgeting can be added
   without touching the rest of the service.
6. **Reply contract**: history is enveloped as JSON (`id`, `role`, `content`,
   `created_at`, `reply_to_id`) and the model must answer with
   `{"content": ..., "reply_to_id": ...}`. `reply_to_id` is **null by default** —
   answering the newest message is an ordinary reply and carries no quote. The model
   sets it only when it deliberately targets an *earlier* message (typically because the
   user asked about something said several messages back); an id that does not resolve
   in the conversation is downgraded to null when the reply is persisted.

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
