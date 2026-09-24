# Two-step verification (SMS / email / authenticator) — decisions, assumptions, open items

This file records the judgement calls behind the implementation, so that nothing
here is mistaken for "verified behaviour" later.

## Delivery methods

A code travels by **SMS** (SMS.ir) or **email** (Resend); a third method, **TOTP**, is
not sent anywhere at all — the user's own authenticator app generates it.

`app/core/contact.py` owns the vocabulary — a *method* (`SMS` | `EMAIL` | `TOTP`) and the
*destination* it needs (`SentOtpMethod` is the subset that has one) — and
`app/services/otp_delivery.py` is the only place that picks a provider. Everything else
(generation, hashing, expiry, attempts, rate limits) is method-agnostic, so a new channel
means a new service and one branch, not a second OTP implementation; TOTP reuses the
challenge machinery unchanged and differs only in *how* a code is judged.

- `USERS.PREFERRED_OTP_METHOD` is **nullable with no DB default**, so NULL means `SMS`.
  Every account that enabled 2FA before email existed is an SMS account, with no data
  migration. An unrecognised value (an out-of-band edit) **fails closed** with 503
  instead of guessing a channel.
- Verifying a *first* contact also sets the preference to that channel — otherwise a
  user who enabled 2FA by email would be left with a default pointing at a mobile number
  that does not exist, and could not log in at all. Verifying a *second* contact leaves
  the preference alone, so adding an email never silently moves where codes go.
- `PATCH /me {preferred_otp_method}` only accepts a channel with a verified destination,
  for the same reason.
- **A contact belongs to one account.** `UK_USERS_MOBILE_NUMBER` and `UK_USERS_EMAIL`
  (added by the project owner on 2026-09-24) mean two accounts can never verify the same
  number or address, so neither can receive the other's codes. The check runs *before*
  the send, so no code goes out to a contact that could not be attached, and a lost race
  surfaces as `409` rather than a 500. It does tell an authenticated caller that an
  address is already in use — that is inherent to enforcing uniqueness at verify time.
- **Changing a contact is verifying a new one.** `/me/otp/enable` + `/me/otp/verify` with
  a different destination replaces the stored one for that method and leaves
  `PREFERRED_OTP_METHOD` alone — swapping a number is not a change of channel. Nothing is
  written until the new contact answers a code, so the old one keeps receiving codes until
  then; and once replaced, the old value is free for another account.
- Addresses are stored **canonical: lowercase, ASCII only**. `VARCHAR2` uses byte
  semantics, so a longer internationalized (EAI) address could exceed the column in bytes
  even when it fits in characters — the same reasoning that forces ASCII digits in
  `normalize_mobile`. Case folding matters because Oracle comparisons are case-sensitive,
  and the "already enabled for this destination" check depends on it.

## Authenticator apps (TOTP)

Parameters are the ones every app assumes — **SHA1, 6 digits, 30-second period** — and are
stated explicitly in `app/services/totp_service.py` rather than left to library defaults.
`pyotp` does the cryptography; nothing here reimplements HMAC or the truncation step. The
accepted window is the **previous, current and next period only** (`valid_window=1`, RFC
6238's recommended drift allowance), which is also what the `otpauth://` URI implies when
it omits `digits`/`period`/`algorithm`: those are the documented defaults, so an app that
honours the standard and an app that ignores those parameters agree.

**Enrolment is not enabling.** `POST /me/totp/enable` generates a secret, stores it, and
returns the `otpauth://` URI plus the Base32 key once. `OTP_ENABLED` is untouched: a
stored secret proves nothing until a code generated from it comes back, which is what
`POST /me/otp/verify` checks. That confirmation goes through the *same* challenge as a
sent code (same expiry, same attempt budget, single use) — the challenge itself says how a
code must be judged, so the OTP service needs no second verification path.

- **The secret comes from the library's CSPRNG** (`pyotp.random_base32()`): 20 random bytes
  = 32 Base32 characters, exactly the width of `USERS.TOTP_SECRET`. The generator takes no
  arguments at all, so nothing about the account (id, username, email, password, timestamp)
  can influence it; tests assert both that signature and uniqueness across generations.
- **No new column and no `TOTP_ENABLED` flag.** `TOTP_SECRET` (existing) holds the secret,
  `OTP_ENABLED` (existing) says whether a second factor is required. "Has a secret" is
  reported to the account's own owner as `authenticator_enrolled` — a derived boolean,
  never the value — so a client can offer the method without being able to generate a code.
- **`USERS.TOTP_SECRET` is `CHAR(32)`**, which Oracle blank-pads. `stored_secret()`
  (`app/core/contact.py`) is its only reader; it strips, and treats an empty column as
  "nothing enrolled" rather than as a value to check a code against.
- **Enrolling again replaces the secret**, so a re-enrolment invalidates the previous app
  entry instead of leaving two working ones: the old key stops working the moment the new
  one is stored, and confirming the new key is what installs it.
- **Nothing is sent for a TOTP challenge.** It never reaches `OtpDeliveryService.send`, so
  no SMS or email goes out when a user switches to their authenticator, and the daily
  *send* cap is not spent (`claim_totp(..., sends_message=False)`): an authenticator user
  signing in from several devices must not be locked out of their own account by a limit
  that exists to bound messages and their cost.
- **The login-challenge cooldown still applies** (`throttled=True` when a login opens a
  challenge): that is what keeps an attacker who already holds the password from grinding
  five-code attempts at line speed. Enrolment passes `throttled=False` — it is neither a
  login attempt nor a resend, and throttling it would delay the login that follows it.
- **The secret and the URI are shown once and never persisted client-side.** They live in
  React state for the length of the enrolment and are dropped when it ends; they are never
  written to `localStorage`/`sessionStorage`, and no endpoint returns them again (`GET /me`
  carries only `authenticator_enrolled`). They are never logged either: the only lines
  about enrolment carry the user id and whether 2FA was already on.
- **A user with an authenticator enrolled but a different default keeps that default.** As
  with a second contact, adding a method does not move `PREFERRED_OTP_METHOD`; it can be
  changed in settings, or chosen for a single login at `POST /auth/login/otp/method`.

## The clock an authenticator code is checked against

A TOTP code is a function of the current time, so the server's clock *is* part of
verification. `app/services/time_service.py` keeps a corrected clock: it asks `NTP_SERVER`
(default `ntp.time.ir`) for the time over NTP (UDP, via `ntplib` — not HTTP), stores the
**offset** between that server and the local clock, and answers every later question from
local time plus that offset. `TotpService` knows only about the corrected clock — it never
mentions NTP — and verification is the only place the clock matters at all.

- **No network on the hot path.** `get_current_unix_time()` is arithmetic; verification
  never contacts the NTP server. A synchronization is a deliberate, periodic act. Measured
  live: one synchronization at startup, then one per `NTP_SYNC_INTERVAL_SECONDS` (default
  3600 s) — and a full enrolment-plus-login browser run added none. A test asserts exactly
  that, counting calls on a fake client: one round trip for the whole test, and it is the
  one the test asks for.
- **The delay-adjusted calculation is the protocol's own** (`ntplib` implements
  `((recv - orig) + (tx - dest)) / 2`), and a sample whose round trip exceeds
  `NTP_MAX_DELAY_SECONDS` (default 1 s) is rejected: a slow answer bounds the sample's
  accuracy to roughly half the delay.
- **The offset lives in memory only.** It is a property of this process's clock, not of the
  data, and it is learned again within an interval; no table is involved. Unlike the
  in-process *challenges*, per-process clock state is harmless across workers: each process
  measures the same machine clock.
- **A failure never replaces a valid offset**, and the last good one is kept. Retries use a
  shorter `NTP_RETRY_INTERVAL_SECONDS` (default 60) so an unreachable server is picked up
  promptly without being hammered.
- **Staleness is bounded and visible.** An offset older than `NTP_MAX_OFFSET_AGE_SECONDS`
  (default 6 h) stops being trusted: `is_usable` goes false, `status` reads `stale` (versus
  `synchronized` and `never_synchronized`), and TOTP verification **refuses with 503**
  instead of checking a code against a clock nobody has confirmed. The refusal logs the
  status and the offset's age — "wrong code" and "no trustworthy time" look identical to
  the user, and only one of them is their problem. Age is measured on a **monotonic** clock,
  so a wall-clock jump cannot make a stale offset look fresh.
- **At startup, a failed sync is not a failed start.** Chat, password login and SMS/email
  codes do not depend on NTP, so the application starts and the loop keeps retrying; only
  authenticator verification is refused (503, with the log line above) until a sync
  succeeds. Nothing pretends synchronization happened — `status` reports
  `never_synchronized` and the log says so.
- **One process, one clock** (`TimeService.instance`). The offset is mutable state, so the
  instance the synchronization loop fills must be the instance a request reads. This was a
  real defect during development, not a hypothetical: the dependency was `lru_cache`d on the
  settings object, and because `functools` keys a positional call differently from a keyword
  call, the loop synchronized one instance while FastAPI handed every request a second,
  never-synchronized one — so every authenticator code was refused with a 503. A regression
  test now asserts both call shapes return the same object.
- **Assumption**: the server's clock is wrong by less than about one period. The offset
  corrects *drift and skew*, not an arbitrarily wrong system clock; the application does not
  set the OS clock, so a machine an hour off needs its clock fixed.
- **Assumption**: `ntp.time.ir` is reachable from the deployment. It answered during
  development (offset ≈ 0.28 s, round trip ≈ 30 ms); if it does not, authenticator
  verification is unavailable until a reachable `NTP_SERVER` is configured.

## Swapping method during a login

`POST /auth/login/otp/method` re-sends to the other verified channel **for that login
only**. It is unauthenticated (the user has no token yet), so:

- The request names a **channel, never an address** — the destination always comes from
  the account row. A stolen `challenge_id` can therefore only cause a send to the
  victim's own verified destination, and the daily cap bounds how many.
- Issuing is **two-phase**: `claim()` reserves the send slot and mints the code, the
  caller sends, and only then does `commit()` install the challenge — replacing the
  previous one. Nothing the user is already holding is touched before that point, so a
  switch that fails at any step (no destination, cooldown, provider down) leaves the
  code they already have **still valid**. `commit(replaces=…)` additionally refuses if
  the challenge being replaced has already gone (a concurrent switch), rather than
  handing back an id that is dead on arrival.
- The cooldown is keyed per **(user, method)**, so switching is immediate while resending
  on the same channel is still throttled. The daily send cap stays **per user**, so
  switching cannot buy extra sends: worst case is 2 sends/minute, still ≤10/day, and the
  attempt budget (10 sends × 5 guesses) is unchanged. This is what makes the switch
  acceptable rather than a bypass.
- The saved preference is never touched by a switch.

## Resend contract (assumption — needs one live request)

- `resend.Emails.send_async({"from", "to", "subject", "html"})` through the official SDK.
- The bodies are **two templates in `app/services/email_service.py`** — one for a
  sign-in code, one for confirming an address — rendered as HTML with inline styles and
  a table layout (no external stylesheet, no images, no web fonts, nothing user-supplied
  to escape). A `<style>` block adds dark-mode colours and tighter padding on small
  screens; every rule there is an override, so a client that ignores it still renders a
  readable light email. An earlier revision sent a **published Resend template referenced
  by alias** instead; that route was dropped, and `template` may never be passed together
  with `html` (the API rejects the combination).
- **The two purposes differ on purpose.** `Purpose` (`login` | `enable`, in
  `app/core/contact.py`) decides the wording, so a code that confirms an address does not
  arrive looking like a sign-in attempt the user never made. The expiry line is computed
  from `OTP_CODE_TTL_SECONDS`, so the email cannot claim a lifetime the code does not have.
- **Assumed**: the sending domain `mail.erfancodes.ir` is verified in Resend. If it is
  not, every send is rejected, the failure is logged as `email send failed` with the
  provider's `code`/`error_type` (never the recipient or the code), and the caller sees 503.
- The SDK's `resend.api_key` and async HTTP client are **process-wide globals** that the
  SDK also populates from the environment at import. `EmailService` assigns both from
  `Settings` on construction, so configuration stays in one place and a developer's
  `RESEND_API_KEY` cannot leak into a test run.
- Every SDK failure surfaces as `resend.exceptions.ResendError` (transport and timeout
  failures are wrapped as `error_type="HttpClientError"`), **except**
  `resend.exceptions.NoContentError`, which subclasses plain `Exception`. Both are caught
  and mapped to 503; a provider 429 is not passed through as the user's own rate limit.
- Delivery is not tracked, exactly as with SMS: Resend accepting the request means the
  message was queued, not that it landed in an inbox.

## Mobile number representation

`USERS.MOBILE_NUMBER` is `NUMBER(10)`, so it physically cannot hold the
international form (`989123456789`). The canonical stored value is therefore the
**local 10-digit number** (`9123456789`), and that is also what the API accepts:

- The UI shows a fixed, non-editable `+98` prefix and the user types `xxx xxx xxxx`.
- `POST /me/otp/enable` takes the digits (separators are tolerated and stripped) and
  rejects anything with a country code or a leading zero, with a message saying so.
- `+98` exists only for display (`format_mobile` / `mask_mobile`).
- SMS.ir receives the **local 10-digit** number (`"mobile": "919xxxx904"` in their
  documented example).

Validation uses an explicit ASCII digit class. Python's `\d` matches Persian and
Arabic-Indic digits, so a number typed on a Persian keyboard would otherwise pass
validation and then either break the `NUMBER(10)` bind or silently become a
different number.

## SMS.ir contract (assumption — needs one live request)

Taken from the account owner's documentation excerpt:

- `POST https://api.sms.ir/v1/send/verify`, header `x-api-key`, camelCase body
  `{"mobile", "templateId", "parameters": [{"name", "value"}]}`.
- **Assumed** parameter names for template `601570`: `USERNAME` and `CODE`. If the
  template's placeholders are named differently, SMS.ir will reject the request
  (status 16/117/124) and only `POST /me/otp/enable` will be affected — enabling
  fails, nothing is stored, and the error is visible in the server log as
  `sms rejected`.
- `status == 1` is success. Any other status, a non-2xx HTTP status, a non-JSON
  body or a transport error is treated as a failure: no code is considered sent, no
  flag is flipped, and the user gets a generic message.
- Delivery is not tracked. SMS.ir accepting the request means exactly that — not
  that the handset received it.

## The OTP history (OTP_LOG) is an audit trail, not the store

`OTP_LOG` was added by the project owner (see `docs/database.md`). The application
inserts a row when a code is issued and sets `CONSUMED`/`CONSUMED_AT` when it is used,
but **verification still happens in memory**: the table has no challenge id, so a
login's `challenge_id` cannot be tied to a row, and nothing that could recover a code
should ever be written down.

- **A row means a code was sent**, not that one was attempted: it is written only after
  the provider accepts the message (the same point at which the challenge is committed).
  A failed send leaves no row.
- **The only thing stored about the code is a hex HMAC-SHA256.** The key is a per-process
  secret, so the value cannot be reversed even by someone holding the database, and it is
  deliberately *not* comparable across restarts. It records that a code existed; it is not
  a lookup key.
- **Rows are matched by `(USER_ID, PURPOSE)`, newest unconsumed first.** That is sound
  because the OTP service keeps one live challenge per user, so the newest unused row for
  a purpose is the code that was just verified. Older rows stay at `CONSUMED = 0` — those
  codes were superseded or expired, and "issued, never used" is exactly what happened.
- **The history is written in its own short session and never fails the flow.** A full or
  broken `OTP_LOG` logs a warning and lets the login proceed: failing an authentication
  because an audit write failed trades a working product for a record of it. That is also
  why each write has its own session — sharing the request's would let a failed audit
  write poison the transaction the flow is still using (and an ORM rollback would expire
  objects the response still needs).
- `PURPOSE` names are chosen to read well in that history: `login` and `verify_contact`
  (a code confirming a mobile number *or* an email — `METHOD` says which).

## Temporary OTP state is in-process

There is no OTP table and the application never alters tables, so challenges live in
`OtpService` — the same in-process approach as the existing login throttle. Two
consequences:

- **Run one worker, or use sticky sessions.** With several workers a code issued by
  one process is unknown to the next, and the second login step fails for a
  legitimate user. Replace with a shared store (Redis) before scaling out.
- A restart invalidates outstanding codes (they expire in two minutes anyway).

## Security properties implemented

- Codes are 6 digits from `secrets`, stored only as an HMAC-SHA256 (never
  plaintext), compared with `hmac.compare_digest`, expire after 120 s, are
  single-use, and die after 5 wrong guesses.
- A challenge is bound to one user and one purpose ("login" or "enable"), and the
  authenticated verify calls check the binding.
- `otp_enabled = 1` with no usable destination for the default method **fails closed**
  (503) rather than falling back to password-only login *or* to the other channel: the
  user chose that method, so a silent substitution would send their code somewhere they
  did not ask for.
- An incorrect or expired code is **400, never 401**: the frontend signs the user out
  on any authenticated 401, so a mistyped code would otherwise eject them.
- The login response for a 2FA account deliberately carries **no destination hint** —
  revealing the phone suffix or address to someone holding the password is the attack
  the second factor exists to stop. It does name the *channel* and whether an
  alternative exists, which is the minimum the client needs to describe the next step
  and to offer a switch; that is a much weaker disclosure than the contact itself.
- Disabling two-step verification discards any code still in flight.
- Codes are never returned by the API and never logged; the SMS service logs only
  the provider's numeric status and `messageId`, and the email service only the
  Resend message id and error type.

## Known limitations

- **Enabling 2FA does not revoke existing sessions.** Access tokens are stateless
  JWTs and this codebase has no revocation, so a stolen token keeps working. 2FA
  protects future logins, not sessions already issued.
- **Disabling 2FA does not require the password** (it is an authenticated settings
  action, as specified). Anyone who can act as the user — a stolen token, an XSS —
  can therefore turn the second factor off. Requiring the password here would be a
  reasonable hardening step; it was left out to match the requested behaviour.
- The per-method send cooldown is shared by the enable and login flows, so a user who
  has just enabled 2FA by SMS or email (or just added such a contact) may be asked to wait
  a minute before the first code on that channel. The other channel is immediately
  available. An authenticator is unaffected at enrolment and throttled at login — see the
  TOTP section.
- **A TOTP login challenge cannot be reissued within the cooldown minute.** It exists to
  bound an attacker who already has the password to five attempts per minute, and it costs
  a legitimate user little: the window accepts a code from any of three periods, and
  retrying the *same* challenge is bounded only by the attempt budget.
- `PATCH /me` cannot *clear* `default_model_id` (its schema treats `null` as "not
  provided"), so a default model can be changed but not removed.
- `MOBILE_NUMBER` and `EMAIL` are unique per account, so a contact can be attached to
  one account only. Uniqueness is enforced on the *verified* value: an unverified address
  can still be typed into the enable form (the code is sent before anything is written),
  which is what the anti-flooding note above is about.
- Switching method invalidates the code already delivered on the other channel. That is
  inherent to keeping exactly one live challenge per user (which is what bounds the
  attempt budget); the UI clears the code input and names the new destination so the
  user is not left typing a dead code. A switch that *fails* does not: the replacement
  is only installed once the provider has accepted it.
- **Verifying a second contact rewrites where the first one points.** Once 2FA is on,
  `POST /me/otp/enable` accepts any contact the caller can receive a code at, and
  verifying it overwrites `USERS.EMAIL` / `USERS.MOBILE_NUMBER` for that channel (the
  *default method* is deliberately left alone). Anyone acting with a stolen token can
  therefore point the account's codes at an address they control and lock the real owner
  out. This is the same class of exposure as the documented "disabling 2FA needs no
  password" item below — it is pre-existing in shape (the SMS path always worked this
  way), and it removes no capability the attacker did not already have, but it is
  quieter. Closing it needs a re-authentication step (current password, or a code to the
  *existing* contact) before enabling or replacing a destination on an account that
  already has 2FA on.
- **Enrolling an authenticator needs no re-authentication.** Anyone acting with a stolen
  token can provision an authenticator they control and confirm it with a code from it,
  which adds a method that is then offered at sign-in (it cannot move a default that already
  exists). This is the same class as the second-contact item above, and the same hardening
  step — re-authentication before changing a second factor — would close both.
- **Registration is unverified and `/me/otp/enable` sends to a caller-chosen address.**
  The daily cap is per user, so N throwaway accounts can send N×10 messages a day to any
  address, from the project's own verified sending domain — with the bounce and
  complaint exposure that brings. A per-destination or global send cap would bound it;
  the per-user cap alone does not.
