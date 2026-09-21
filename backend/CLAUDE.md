# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

FastAPI backend for an AI chat application: JWT auth, AI characters
(personas), conversations, SSE-streamed AI replies, and long-term memories. It runs
against an existing Oracle database whose schema is the source of truth — the app
never creates or alters tables. Python 3.13+, dependencies managed with `uv`.

`README.md` holds the full API reference and behavior details; `docs/database.md`
documents the discovered Oracle schema. This file covers the architectural invariants
a future change must not break.

## Commands

All via `uv` from the `backend/` directory (dev tools are in the `dev` dependency group, installed
by default):

```bash
uv sync                                  # install deps + dev tools into .venv
uv run uvicorn app.main:app --port 8000  # run the API (Swagger at /docs)

uv run pytest                                   # full suite — SQLite in-memory, no Oracle needed
uv run pytest tests/test_context.py             # one file
uv run pytest tests/test_context.py::test_foo   # one test

uv run ruff check app tests              # lint
uv run ruff format app tests             # format (double quotes, line length 100)
uv run ty check app                      # type checking (tests/ excluded)
```

`uv run python scripts/verify_oracle.py` is a separate live-Oracle verification:
read-only model↔schema mapping check plus a full API flow against Oracle using the mock
provider. The pytest suite never touches Oracle.

## Architecture

Strict layering; dependencies point downward only:

```
routes (app/api/routes) → services (app/services) → repositories (app/db/repositories) → SQLAlchemy models (app/db/models)
```

`app/api/dependencies.py` wires layers together (settings, session factory, services,
auth). `app/schemas` (Pydantic v2) holds request/response contracts, including SSE
payloads. `app/ai` is the provider abstraction — independent of HTTP/DB layers.
`app/core` holds settings, security (Argon2id + JWT), structured logging, time helpers.

### AI streaming flow (the core design decision)

`POST /conversations/{id}/messages` returns an SSE `StreamingResponse`. The flow in
`app/services/ai_service.py` is deliberately session-lean — no DB connection is held
while the provider streams:

1. **Pre-flight** (`prepare_message`, runs before the response starts so failures are
   real HTTP status codes): session #1 — ownership check + persist user message;
   session #2 — load character/model/history/memories, build context via the pure
   functions in `app/ai/context.py`.
2. **Stream** (`stream_response`): no DB access. The AI replies with a JSON
   envelope (`{"content": ..., "reply_to_id": ...}`), so provider `DeltaEvent`s
   are buffered and only the progressively extracted `content` text is forwarded
   as `message.delta` — the raw JSON never reaches the client. Non-JSON output
   is held and resolved at completion (`parse_completion` in `app/ai/context.py`,
   raw-text fallback). Client disconnect (`request.is_disconnected()`,
   `GeneratorExit`, `ClientDisconnect`) cancels the provider stream via
   `aclosing()` and persists nothing partial.
3. **Finalize** (`_persist_assistant_message`): session #3 — validate the AI's
   `reply_to_id` against the conversation (foreign/missing ids degrade to null),
   persist assistant message + `MESSAGE_GENERATIONS` telemetry in one
   transaction, then emit `message.completed`.

Provider protocol (`app/ai/base.py`): `stream_chat(ChatRequest) -> AsyncGenerator`
yielding `DeltaEvent | CompletionEvent | ErrorEvent` — only these types cross the
provider boundary. Adding a provider means implementing the protocol and registering it
in `app/ai/registry.py` (`create_provider`). Unknown provider names fall back to the
OpenAI-compatible implementation (covers DeepSeek and custom gateways).

### Provider/model resolution

`AI_PROVIDER=database` (default) resolves provider → endpoint (base URL + API key) →
model name from the `PROVIDERS` / `AI_MODELS` / `AI_ENDPOINTS` tables per conversation
(`AIService._resolve_endpoint`). `AI_PROVIDER=mock|openai|anthropic` bypasses the DB
and uses the `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL` env fallbacks.

### Auth, ownership, errors

- `get_current_user` resolves the JWT user in its own short-lived session (from the
  session-factory dependency), never the request-scoped `get_db` session — streaming
  routes must not hold a connection. It sets `request.state.user_id` for the access-log
  middleware.
- Ownership is enforced at the repository layer (`user_id` in every query); foreign
  IDs return 404, indistinguishable from nonexistent. Soft deletion (`STATUS='DELETED'`
  on conversations) is filtered the same way.
- Intentional errors are `AppError` subclasses in `app/exceptions/__init__.py` (each
  maps to a status code); the handler in `app/main.py` renders `{"detail": ...}`.
  Anything else → generic 500 with details logged server-side only. Provider failures
  mid-stream are `error` SSE events, not HTTP errors.
- The login throttle (5 failures → 60 s, `429` + `Retry-After`) is in-memory per
  process — not shared across workers.
- **Two-step verification** (`app/services/two_factor_service.py`, `otp_service.py`,
  `sms_service.py`): the second step never issues a token — `POST /auth/login` returns
  `otp_required` + a `challenge_id` and `POST /auth/login/otp` completes it. Mobile
  numbers are stored in the local 10-digit form (`MOBILE_NUMBER` is `NUMBER(10)`) and
  sent to SMS.ir that way. OTP challenges live in-process like the login throttle
  (**single worker or sticky sessions**; see `docs/otp-2fa-notes.md`). An incorrect or
  expired code is **400, never 401** — the frontend signs out on any authenticated 401.
  Never log or return a code, and never send a code before the password is verified.

## Invariants & gotchas

- **Never create, drop, truncate, or alter Oracle tables.** Models in `app/db/models`
  map the existing schema: `__tablename__` is the UPPERCASE Oracle name (the Oracle
  reserved word `NAME` is the column `NAME_`, Python attribute `name_`). Use
  dialect-neutral column types so models also work on SQLite for tests. Model changes
  must match `docs/database.md`.
- The engine is created at import time in `app/db/database.py` from `get_settings()` —
  env vars must be set before importing `app.*` (see `scripts/verify_oracle.py`).
  `oracledb.defaults.fetch_lobs = True` is set globally so CLOBs arrive as strings.
- Settings are frozen pydantic-settings (`get_settings()` is `lru_cache`d). Tests
  override dependencies via `app.dependency_overrides` — `get_db`, `get_settings`,
  `get_session_factory`, and `get_provider_factory` (injected with a scripted
  provider) — see `tests/conftest.py`, which also provides helpers (`auth_user`,
  `seed_conversation`, `parse_sse`, …). Reuse them rather than re-inventing.
- Logging: `structured(logger, level, msg, **fields)` from `app/core/logging.py`
  (key=value fields). Never log passwords, tokens, API keys, or message contents.
- Context building in `app/ai/context.py` is pure (no I/O): persona prompt + top
  memories + budgeted history (~4 chars/token heuristic, newest message never
  dropped). Every history message is sent to the AI as a JSON envelope carrying
  id/role/content/created_at/reply_to_id (`render_message_body`, with
  `render_created_at` formatting the timestamp as UTC ISO-8601); the AI's reply envelope
  is parsed by `parse_completion` and streamed through
  `extract_streamed_content`. Keep it pure so it stays unit-testable
  (`tests/test_context.py`).
- `.env.example` lists every setting with empty (or built-in default) values —
  copy it to `.env` and fill in real values (`.env` is gitignored; `JWT_SECRET`
  and the Cloudinary credentials must be set for those features to work).
