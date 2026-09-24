# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Full-stack AI chat application in a single repository. Two components make up
the product: `backend/`, a FastAPI server that serves a JSON API with
SSE-streamed AI replies, and `frontend/`, a React 19 + Vite + TypeScript
single-page chat app that consumes those streams.

`backend/CLAUDE.md` is the authoritative backend guide — architectural
invariants, layering, and gotchas. Read it before changing backend code. The
root README holds the full feature/architecture overview.

## Commands

### Backend (`backend/` — Python 3.13, deps via `uv`)

```bash
cd backend
uv sync                                  # install deps + dev tools
uv run uvicorn app.main:app --port 8000  # run the API (Swagger at /docs)

uv run pytest                                   # full suite — SQLite in-memory, no Oracle needed
uv run pytest tests/test_streaming.py::test_stream_success_flow   # one test
uv run ruff check app tests              # lint
uv run ruff format app tests             # format (double quotes, line length 100)
uv run ty check app                      # type checking (tests/ excluded)
```

### Frontend (`frontend/` — React 19 + TypeScript + Vite)

```bash
cd frontend
npm run dev      # Vite dev server; proxies /api → http://localhost:8000
npm run build    # tsc -b && vite build (type-check + bundle)
npm run lint     # oxlint
npm run e2e      # Playwright end-to-end verification against a live backend
```

## Architecture

```
backend/  FastAPI: routes → services → repositories → SQLAlchemy models (layered; deps point down)
frontend/ React 19 SPA: feature folders, TanStack Query for server state, SSE via fetch streaming
```

### Backend essentials

- **AI streaming flow** (`app/services/ai_service.py`): `POST /conversations/{id}/messages`
  persists the user message pre-flight, streams SSE (`message.created` →
  `message.delta`* → `message.completed`), then persists the assistant message.
  Deliberately session-lean: no DB connection is held while the provider streams.
  Client disconnect cancels the provider stream and persists nothing partial.
- **Structured message contract** (`app/ai/context.py`, pure functions):
  every history message is sent to the AI as a JSON envelope
  `{"id", "role", "content", "created_at", "reply_to_id"}` — ids so the AI can reply
  by id, `created_at` as UTC ISO-8601 so it can tell how far apart messages were sent.
  When the client sends its timezone/offset, a `CURRENT TIME` line is added to the
  system prompt so the model also knows what time it is where the user is. The AI
  replies with `{"content", "reply_to_id"}`; the content is
  extracted progressively from the streamed JSON (`extract_streamed_content`)
  and the reply is parsed at completion (`parse_completion`, raw-text
  fallback). Never change the wire format without updating both
  `render_message_body` / `MESSAGE_FORMAT_HINT` and the frontend.
- **Provider abstraction** (`app/ai/base.py`): `stream_chat(ChatRequest)` yields
  `DeltaEvent | CompletionEvent | ErrorEvent`; OpenAI-compat (DeepSeek),
  Anthropic, and mock providers. Providers pass `content` through untouched.
- **Ownership**: enforced at the repository layer; foreign ids return 404.
  Oracle schema is the source of truth — the app never alters tables.

### Frontend essentials

- **Server state**: TanStack Query (`useMessages`, `useConversations`);
  **in-flight AI replies** live in a module-level store (`streamingStore.ts`,
  `useSyncExternalStore`) so streams survive conversation switches.
- **SSE**: `sendMessageStream` (frontend/src/api/messages.ts) consumes the
  streamed response body via fetch + `parseSseStream`; deltas append to the
  streaming bubble, `message.completed` removes it and invalidates queries.
- **Reply flow**: any message (user or assistant) can carry `reply_to_id`;
  `MessageList` renders the quote by looking the target up in loaded pages
  (degrades gracefully when the target is older than the loaded window).
- **Settings & two-step verification**: the sidebar footer opens
  `features/settings/SettingsModal` — username (unique across accounts; the API answers
  `409` when taken, and tokens survive a rename since they carry the user id), display
  name, default model, SMS two-step verification. When 2FA is on, `POST /auth/login`
  returns `otp_required` instead of a token and `AuthPage` switches to a code prompt;
  the session user is refreshed through `authSession.updateSessionUser`. Rejections
  there are 400-level on purpose — the client signs out on any authenticated 401. The
  same dialog holds the theme choice (light/dark/system) from `theme/themeStore.ts`,
  which sets `data-theme` on `<html>` for `styles/tokens.css` (index.html applies it
  pre-paint).

## Gotchas

- Backend: `uv run` from `backend/` (root has no Python env). Frontend: `npm`
  from `frontend/`. Each component directory keeps its own `.gitignore`, README, and env files
  (both have a `.env.example`; copy it to `.env` and fill in real values —
  `JWT_SECRET` and the Cloudinary/SMS keys have no usable defaults).
- Tests: the backend suite never touches Oracle (in-memory SQLite + a scripted
  provider — see `tests/conftest.py`). One pre-existing failure,
  `tests/test_personas.py::test_create_persona_validation`, fails identically
  in the upstream repo and is unrelated to current work.
- Frontend types mirror the backend Pydantic schemas one-to-one
  (`frontend/src/types/api.ts`); timestamps are naive-UTC ISO strings — parse
  with `parseIsoUtc` from `utils/dates.ts`.
