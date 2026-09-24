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
    cloudinary.ts          #   signed upload: signature from the API, file straight to Cloudinary
  session/authSession.ts   # JWT + user in localStorage, expiry, reactive subscription
  theme/themeStore.ts      # light/dark/system choice → data-theme on <html>
  utils/dates.ts errors.ts # naive-UTC ISO parsing (backend stores UTC without zone), errors
  utils/cloudinary.ts      # avatar delivery URLs: on-the-fly resize + auto format/quality
  utils/cropImage.ts       # 1:1 crop → 512×512 WebP in the browser (canvas, no upload)
  components/              # shared UI: Avatar, Modal, Spinner, EmptyState, ErrorState,
                           #   ToastHost (+ toastStore), inline SVG icon set
  features/
    auth/AuthPage          # sign-in / account creation (password typed twice), plus the
                           #   code step for accounts
                           #   with two-step verification (and the switch to the other
                           #   delivery method)
    conversations/         # sidebar, list items (rename/delete), infinite list query
    characters/            # cached character lookup (avatars), create + profile dialogs,
                           #   avatar picker (crop → upload to Cloudinary)
    models/                # shared ['models'] query (picker + settings)
    settings/SettingsModal # profile (username, display name, default model), theme,
                           #   two-step verification (SMS or email, default method)
    personas/              # user personas: cached list + create-persona form modal
    newConversation/       # step-by-step new-chat wizard: character → model → persona,
                           #   with a clickable stepper (one step's UI at a time)
    chat/                  # ChatView, MessageList, MessageComposer,
                           #   useSendMessage (SSE), useMessages (before_id paging),
                           #   useMessageGestures (tap/hold menu, swipe to reply),
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

- **Auth**: all endpoints except `/auth/register`, `/auth/login`, `/auth/login/otp` and
  `/auth/login/otp/method` need a JWT Bearer token. The client attaches it automatically.
  `AppShell` also refreshes the profile once on load (`GET /me`), because the session —
  and the user object inside it — is persisted in localStorage and can outlive a deploy
  that adds a field to it; until that refresh lands, a value the UI cannot interpret is
  read the way the API reads it rather than treated as a crash (see `utils/otpMethod.ts`).
  A `401` clears the local session and returns the user to the sign-in screen, and
  token expiry follows the API's `expires_in` (minutes). When the account has
  two-step verification on, `POST /auth/login` returns `otp_required` + a
  `challenge_id` instead of a token, the sign-in screen switches to a code prompt,
  and the token only arrives from `POST /auth/login/otp`. A wrong or expired code is
  a 400 — never a 401, because this client would treat that as an expired session and
  sign the user out.
- **Settings**: the sidebar footer opens a settings dialog — username, display name and
  default model (`PATCH /me`), password (`POST /me/password`, with the new password typed
  twice and matched in the form before it is sent), theme, and two-step verification
  (`/me/otp/*`). The
  username field only sends a value when it actually changed and is validated client-side
  to the API's rules; a name another account already holds comes back as a `409`, which
  the dialog reports inline (it is never a 401, so the user is not signed out). A
  successful rename updates the cached session, so the sidebar footer reflects it
  immediately — and since the token carries the user id, the session survives it.
  Enabling 2FA sends a code to a **mobile number** (10 digits after a fixed `+98` prefix;
  the stored value is the canonical local number, never the formatted one) or an **email
  address**, and the flag only turns on once that code is verified. Verifying the first
  contact makes it the default delivery method; verifying the other one later adds it
  without moving the default, and only then does the "Send codes to" selector appear
  (`PATCH /me {preferred_otp_method}`). Each verified contact is listed with a **Change**
  action that opens the same form for a replacement — the old number or address keeps
  working until the new one answers a code, and an address another account has verified
  comes back as a `409`. `/me/otp/*` rejections are 400-level for the same
  reason as above, and a successful change updates the cached session via
  `updateSessionUser` so the sidebar reflects it without a reload.
- **Choosing where a login code goes**: `POST /auth/login` names the channel
  (`delivery_method`) and, when the account has a usable second contact, offers
  `alternative_method` — the sign-in screen then shows "Send the code by text message /
  email instead", calling `/auth/login/otp/method`. That choice lasts for that login
  only and never changes the saved default; the response carries no address or number,
  only which channel was used.
- **Theme**: light/dark/system is a per-browser preference in `theme/themeStore.ts`,
  applied as `data-theme` on `<html>` — which is what `styles/tokens.css` keys the
  dark palette off. A pre-paint script in `index.html` applies it before the first
  frame, so a dark-theme user never sees a light flash. "System" follows the OS live;
  an explicit choice wins over it.
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
  without a zone suffix, so all dates are parsed as UTC (`utils/dates.ts`). Each message
  also carries the browser's own timezone and UTC offset (`api/messages.ts`), which the
  backend turns into a `CURRENT TIME` line for the AI — the server only knows UTC, so
  telling it where the user is has to come from the client.
- **New chat wizard**: `features/newConversation` walks character → model → persona one
  step at a time, reusing the same selection UI (and the same `useCharacters` /
  `useModels` / `usePersonas` queries) it always had. The stepper at the top is the
  navigation: it marks the current step (`aria-current="step"`), shows what each step has
  chosen so far, and jumps straight to any step. The character is the only required
  selection, so the later steps stay disabled until one is picked; the model is
  preselected (the user's default when active, else the first) and a persona is optional
  ("No persona"), which is why revisiting a step never invalidates another — the three
  selections are independent, and the primary action ("Start chatting") appears only on
  the last step.

## Assumptions & limitations

- A character's `system_prompt` is the persona **prompt** sent to the AI provider
  (e.g. "NEVER break character…") — application data, never displayed in the UI.
  The separate `description` field is a short human-facing blurb and is shown in
  the character profile dialog. Characters are listed by name + avatar (with a
  colored initial fallback when `avatar_url` is `null`).
- **Avatars are uploaded to Cloudinary**, not pasted as URLs: the browser crops the
  picked image to 1:1, renders a 512×512 WebP (`utils/cropImage.ts`), asks the API
  for a signature and posts the file straight to Cloudinary — the image never passes
  through the backend, and the API secret never reaches the browser. The stored
  `avatar_url` is Cloudinary's `secure_url`; `Avatar` rewrites it on delivery
  (`utils/cloudinary.ts`) so each display size fetches a resized, auto-formatted,
  auto-quality image. Uploads are optional — a character without one shows the
  initial. Requires the backend's `CLOUDINARY_*` settings; unconfigured, the picker
  reports the API's "Cloudinary is not configured" error.
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
