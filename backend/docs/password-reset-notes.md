# Password reset (forgot password) — decisions, assumptions, open items

This file records the judgement calls behind password recovery, so that nothing here is
mistaken for "verified behaviour" later. It assumes the two-step verification machinery
documented in `docs/otp-2fa-notes.md` — the challenge store, the providers, the limits —
and adds only what recovery needs on top of it.

## The shape of the flow

```
POST /auth/password-reset/request   identifier [+ method]  → which methods, and a code if one was sent
POST /auth/password-reset/verify    challenge_id + code    → reset_token (ten minutes, single use)
POST /auth/password-reset/complete  reset_token + password → the new password is set
```

Three steps, and the middle one is what makes the last one safe: **the API verifies the
code itself and remembers that it did**. The client is never asked whether the code was
right, and no request field can assert it — `{"otpVerified": true}` is ignored like any
other unknown field, and there is a test that says so. The password only ever changes for
a caller holding the authorization this API minted, for that one account, within the
window, once.

`verify` changes nothing. It is deliberately not "verify and set the password in one
call": that would put a password in the same request as a code, and would mean a valid
code alone (intercepted from an SMS, say) could set a password without the owner ever
choosing one in a form they can see.

## The authorization is not a session

`secrets.token_urlsafe(32)` — 256 bits of `secrets`, opaque, with no structure to read.
Stored as an HMAC (per-process key) of the token, never the token itself, exactly like an
OTP code, so a dump of the process is not a list of usable resets; a restart invalidates
everything outstanding (they last ten minutes). Bound to `user_id`, to a purpose
(`password_reset`), and to an expiry. Accepted by **one** endpoint: presenting it to
`/me`, `/conversations` or `/memories` is a 401, and there is a test for that.

Spent **before** the write, not after: two requests arriving together cannot both use it.
The cost is that a failure after that point (a database error) means starting over — the
right trade, because the alternative lets a stolen token race the real owner, and a
wrong-but-well-formed password never gets that far (the schema rejects it first, which is
asserted too).

Completing a reset also drops every outstanding authorization for the account and every
challenge of **every** purpose for it (`OtpService.invalidate_user`): the password changed,
so a sign-in code minted under the old one must stop being a way in. The login throttle is
cleared for the same reason, one account at a time — the failures belong to a password
that no longer exists.

## Reusing the verification machinery, not copying it

| Concern | Where it lives |
| --- | --- |
| Code generation, hashing, expiry, attempts | `OtpService` (unchanged), `purpose="password_reset"` |
| Sending by SMS or email | `OtpDeliveryService` (unchanged) |
| Authenticator codes | `TotpService` + the existing `USERS.TOTP_SECRET` |
| Which channels an account can use | `OtpDeliveryService.usable` — the same question sign-in asks |
| The history | `OTP_LOG` via `OtpAudit`, `PURPOSE='password_reset'` |
| Hashing the new password | `PasswordManager` (Argon2id), the same class registration and `/me/password` use |

`app/services/password_reset_service.py` is the new file: it owns the three steps and the
authorization store. It contains no code generation, no provider call, and no hashing of
its own.

**The authenticator secret is reused, never replaced.** Recovery by TOTP checks a code
against the `USERS.TOTP_SECRET` that already signs the user in; it does not mint a second
secret and never rotates the existing one (asserted by a test). Follow the same clock
rules as sign-in: an unsynchronized or stale clock refuses with 503 rather than guessing.

## Limits: separate for the flows, shared for the addresses

`OtpService` counts sends per **limit group** (`_LIMIT_GROUPS`): `login` and
`verify_contact` share one group exactly as they always have, and `password_reset` is its
own. So:

- a reset never spends the sign-in cooldown or daily allowance, and a failed sign-in never
  eats the allowance needed to recover the account (both directions are tested, at the
  store and through the API);
- `PASSWORD_RESET_MAX_SENDS_PER_DAY` (5) is deliberately smaller than
  `OTP_MAX_SENDS_PER_DAY` (10): resetting is rarer than signing in, and it is what an
  attacker holding the phone would reach for;
- **the per-destination and per-address windows stay shared.** They exist because a
  number, an address or a network is reachable whichever account asks, and they bound
  messages and abuse rather than one account's budget. A user who has just been sent a
  sign-in code has just received a message, and the next code for that destination —
  whichever flow asks for it — is the one the window is about.

One difference, on purpose: for this flow the **per-address window counts requests, not
sends** (`OtpService.reserve_request_slot`, called before anything is looked up). The
other limits only ever see messages, and a request that sends nothing — an unknown
account, a channel the account cannot use — would leave them untouched. That difference
is observable: once an address had spent the window on real sends, a request that *would*
send would answer 429 while one that would not kept answering 200, which is a way to ask
"does this account exist?". Counting every request makes the two answers identical again.
It is a window of its own rather than the one messages are counted in, so a recovery
sweep cannot refuse that address's own sign-in codes (or the other way round), and
sign-in sends are otherwise untouched: what that window counts is behaviour users already
rely on.

## Account enumeration

The trade-off, stated plainly, because it is a trade-off rather than a guarantee:

- **The answer to `/request` is the same whether or not the account exists** — same status,
  same wording, both with and without a method — and it never names a destination. Not the
  address, not a masked version of it, and no phone suffix either: unlike the sign-in flow,
  the caller here has proved nothing about the account, so the weaker fact ("which channel")
  is not disclosed either. Tests assert the address and any hint field are absent from the
  response body.
- **`methods` is the acknowledged leak.** The spec for this feature asks the UI to offer
  only the methods the account actually has, and that list is exactly what tells a
  determined caller that an account exists and which contacts it has. It is bounded to
  three values and it cannot be closed without dropping the "choose a method" step. An
  unknown account, or one no channel can reach, reports all three — the shape a fully
  equipped account reports — so at least the *unknown* case is not distinguishable by the
  presence of a list.
- The destinations themselves are never returned, so the leak is "this account can be
  reached by email", never "by al***@example.com".
- Usernames are already discoverable: `POST /auth/register` answers 409 for a taken one,
  and that is a deliberate product decision, not something this flow introduced.
- Timing is not equalised (a lookup happens before an answer is produced). The difference
  is a database round trip on a local network against an Argon2-free path; it was judged
  not worth padding with artificial delay, which would slow every honest request. If that
  judgement is wrong for this deployment, the fix is a constant-time floor on the endpoint.

## Choices in the flow itself

- **Which identifier.** `USERS.USERNAME` first, then `USERS.EMAIL` if the identifier
  contains `@` and the username did not match. Both columns are `UNIQUE`, so both resolve
  at most one account. A username may itself contain `@` (`a.b@gmail.com` is a valid
  username), which is why the username wins: it is the application's own identifier, and
  the precedence is stated here rather than left to be discovered.
- **No `HELP_ENABLED` gate.** A verified contact or an enrolled authenticator is what
  makes an account recoverable, whether or not two-step verification is currently on.
  `two_factor_service.disable` keeps verified contacts, and someone who turned 2FA off —
  often because they lost the phone — must not be locked out of their own account.
- **The frontend is never the security boundary.** It carries a challenge id and an
  authorization between its own steps, and it holds no claim the server believes. Opening
  `/reset-password` directly is therefore safe: it is a page with a form, not a permission.
  It is also why the last step is not "…and then you are signed in": the user signs in with
  the new password, through the ordinary flow, second factor and all.
- **A rejected password does not spend the authorization.** The length rules are enforced
  by the request schema, before the service is reached, so a password that is too short is
  a 422 and the token is still good — the user fixes it and resubmits.

## What this does *not* do

- **Sessions are not revoked.** Access tokens are stateless JWTs and there is no
  revocation list or token version column in the schema (see `docs/otp-2fa-notes.md`, the
  same limitation applies to sign-out). After a reset, tokens minted *before* it keep
  working until they expire — up to `ACCESS_TOKEN_EXPIRE_MINUTES` (24 h by default). What
  the reset does guarantee is that no *new* session can start without the new password,
  and that outstanding sign-in codes are dropped. Closing this properly needs a schema
  change (a token version or a session table) and a revocation check in
  `get_current_user`; it is the first thing to do if the threat model includes a stolen
  phone *and* an active session.
- **Reset state is in-process**, like the challenge store and the login throttle: one
  worker or sticky sessions, and a restart loses outstanding resets. Same caveat, same
  fix (a shared store).
- **No email/SMS "reset attempted" notice** to the account owner. The code itself is the
  notice, and the email copy says plainly that ignoring it changes nothing.

## Verification

- `tests/test_password_reset.py` — 41 cases: the three channels end to end, a code that is
  wrong/expired/exhausted, an authorization that is fabricated / a real login token /
  expired / spent twice / invalidated by a newer one, a foreign account's token and
  challenge, the client asserting verification, purpose isolation in both directions
  (a login code cannot be spent here, a reset code cannot complete a login), the generic
  answers, the shared and separate limits, and the hashing of the stored password.
- The rest of the suite is the regression net for the shared machinery: `OtpService`,
  `OTPDeliveryService` and `TotpService` behaviour for sign-in is unchanged, which is what
  the `login`/`verify_contact` limit group preserving its old sharing is for.
