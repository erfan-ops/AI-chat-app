# AI Chat — Frontend

A modern chat application built with **React 19 + TypeScript + Vite**, talking to the
FastAPI backend at `http://localhost:8000`. The flow is:

**Choose a character → choose the AI model → create a conversation → chat.**
(You can also add and pick a **user persona** — how the AI knows you — but chatting
without one is always allowed.)

AI replies stream in live over Server-Sent Events; everything else uses the backend's
REST endpoints. All data (characters, models, personas, conversations, messages) comes from the
API — nothing is hardcoded or mocked.

## Requirements

- Node.js ≥ 20 (developed on Node 24)
- The backend running at `http://localhost:8000` (in this repository, see `../backend`)
- The backend's CORS allow-list includes `http://localhost:5173` (already the default)

## Running

```bash
npm install
npm run dev        # http://localhost:5173
```

Production build preview (works on any port/host, e.g. `npx vite preview --host
0.0.0.0 --port 5931` to reach it from other devices):

```bash
npm run build
npm run preview
```

**How the backend is reached:** the app calls the API same-origin at `/api/…`, and
both the dev server and the preview server proxy that prefix to
`http://localhost:8000` (see `vite.config.ts`). This avoids CORS entirely, so no
backend origin allow-listing is needed for any port or LAN address. Restart the
Vite server after changing `vite.config.ts` for the proxy to take effect.

To point the app at a differently hosted backend, set `VITE_API_BASE_URL` to an
absolute URL at build time (the backend must then allow the app's origin in its
`CORS_ORIGINS`). Only values that are safe to expose to the browser belong in the
frontend's env — the app never sees database credentials or API keys.

Other scripts:

```bash
npm run build      # type-check (tsc -b) + production build
npm run lint       # oxlint
npm run e2e        # full browser E2E against the real backend (needs `npm run dev` running)
npm run e2e:layout # layout/visual-state checks (light, dark, mobile)
```

The E2E scripts drive the real UI in the system Chrome via Playwright and cover:
sign-up → sidebar empty state → characters/models from the API → create conversation →
send message → streamed reply → history after reload → switching conversations →
deleting conversations. They also fail on console errors or failed API requests.
They can target any running instance, e.g. `APP_URL=http://localhost:5932 npm run e2e`.

## Project structure

```
src/
  main.tsx                 # entry: QueryClientProvider + ToastHost
  App.tsx                  # session gate: AuthPage | AppShell
  AppShell.tsx             # two-pane layout + pure-UI state (active chat, drawer, modal)
  styles/tokens.css        # design tokens (light + dark), reset, focus/scroll styles,
                           #   Persian webfont @font-face (Arabic-script unicode-range)
  types/api.ts             # domain types mirroring the backend's Pydantic schemas
  api/                     # the only place that talks HTTP
    client.ts              #   fetch wrapper: base URL, bearer token, ApiError, 401 handling
    sse.ts                 #   minimal SSE parser (POST streams can't use EventSource)
    auth.ts characters.ts models.ts conversations.ts messages.ts
  session/authSession.ts   # JWT + user in localStorage, expiry, reactive subscription
  utils/dates.ts errors.ts # naive-UTC ISO parsing (backend stores UTC without zone), errors
  components/              # shared UI: Avatar, Modal, Spinner, EmptyState, ErrorState,
                           #   ToastHost (+ toastStore), inline SVG icon set
  features/
    auth/AuthPage          # sign-in / account creation (the API requires a Bearer token)
    conversations/         # sidebar, list items (rename/delete), infinite list query
    characters/            # cached character lookup (avatars), create + profile dialogs
    personas/              # user personas: cached list + create-persona form modal
    newConversation/       # character + model picker modal (+ optional persona picker)
    chat/                  # ChatView, MessageList, MessageComposer,
                           #   useSendMessage (SSE), useMessages (before_id paging),
                           #   streamingStore (module-level store for in-flight replies)
e2e/                       # Playwright verification scripts
docs/openapi.json          # snapshot of the backend's OpenAPI spec
public/
  fonts/IranYekanXVF/      # IRANYekanX font package (one 96 KB variable woff2 is used)
  favicon.svg icons.svg
```

**Typography.** Latin text uses the OS system font stack; Persian/Arabic-script text
renders in the bundled **IRANYekanX** variable font, declared in `src/styles/tokens.css`
with a `unicode-range` limited to Arabic-script code points. That range is what keeps
Latin on the system stack even though the family is listed first, and it makes the
browser fetch the font only once Persian text actually appears on screen. To bundle a
face for another script, add a `@font-face` with that script's `unicode-range` and put
its family at the front of the `body` stack.

## API integration notes

- **Auth**: all endpoints except `/auth/register` and `/auth/login` need a JWT Bearer
  token. The client attaches it automatically; a `401` clears the local session and
  returns the user to the sign-in screen. Token expiry follows the API's
  `expires_in` (minutes).
- **Errors**: the backend returns `{"detail": string}` (or a pydantic list for 422).
  The client normalizes both into a user-readable `ApiError`.
- **Sending messages**: `POST /conversations/{id}/messages` persists the user message,
  then streams SSE events — `message.created` → `message.delta`* → `message.completed`
  (or `error`, in which case only the user message exists server-side). Because the
  endpoint is a POST, the app parses the stream from `fetch` rather than using
  `EventSource`. Streams run in a module-level store, so switching conversations
  mid-reply doesn't interrupt anything; persisted messages live in the React Query
  cache and are refreshed via invalidation after each mutation.
- **Pagination**: conversations use `limit`/`offset` (Load more in the sidebar);
  message history pages backwards with `before_id` (older messages load when
  scrolling to the top, with scroll anchoring).
- **Timestamps**: the backend stores naive-UTC `TIMESTAMP` values and serializes them
  without a zone suffix, so all dates are parsed as UTC (`utils/dates.ts`).

## Assumptions & limitations

- A character's `system_prompt` is the persona **prompt** sent to the AI provider
  (e.g. "NEVER break character…") — application data, never displayed in the UI.
  The separate `description` field is a short human-facing blurb and is shown in
  the character profile dialog. Characters are listed by name + avatar (with a
  colored initial fallback when `avatar_url` is `null`).
- The conversation list only exposes a `{id, name}` character brief and no last-message
  preview, so sidebar items show: character avatar, title (or character name), model
  name, persona (as "as …" when one was picked), and relative time of the last activity.
- Reply streaming is incremental over HTTP; the composer stays disabled while the
  character is replying (typing indicator shown), and re-enables right after.
- Conversation deletion is a **soft delete** server-side; the UI treats it as removal
  from the list.
- The app remembers the open conversation in `sessionStorage` (restored on reload,
  cleared on sign-out); there is no URL routing.
- No Markdown rendering: the API streams plain text, which is displayed as-is.
