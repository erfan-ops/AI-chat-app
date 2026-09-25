# Sign in with Google — decisions, assumptions, open items

Same purpose as `otp-2fa-notes.md`: record the judgement calls behind the feature so
that none of them is later mistaken for "verified behaviour", and so the ones that
need a product decision are visible rather than buried in code.

## The flow

```
browser: GIS button → Google account chooser → credential (a signed ID token)
        ↓  POST /auth/google {credential}
API:    verify against Google's keys → sub → find USERS.GOOGLE_SUB
        ├─ found     → sign in (nothing on the account changes but last_login_at)
        └─ not found → create the account, then sign in
        ↓
        the same LoginResponse (or OtpRequiredResponse) POST /auth/login returns
```

The credential is an **ID token**, not an authorization code: Google signs it, the
browser hands it over, and this API verifies it against Google's published keys. That
is why **there is no `GOOGLE_CLIENT_SECRET`** — no code is exchanged, so no secret has
to exist. A server-side authorization-code flow would need one; this is deliberately
not that flow. `GOOGLE_CLIENT_ID` is the public half and is the audience every
credential must carry.

## What is verified, and by whom

`google.oauth2.id_token.verify_oauth2_token` does it — the maintained Google library,
not hand-written JWT parsing. It checks the signature against Google's key set, the
issuer, the audience (this application's client id) and the expiry. Nothing in the
claims is read before that returns.

- **`sub` is the identity.** It is Google's stable per-account identifier. The email
  address is *data a Google account has*, not an identifier — a provider can reassign
  one — so it is never what an account is looked up by.
- **`email_verified` must be true**, otherwise the sign-in is refused. Every address
  this application stores is treated as verified (it is what OTP delivery and the
  uniqueness rules assume), so an unverified one is not written.
- **Failures are split by whose fault they are.** `TransportError` (Google's key set
  could not be fetched) answers **503** — the credential may be perfectly good.
  Everything else (`MalformedError` from a key that does not verify the signature,
  `InvalidValue`, and the library's `ValueError`s for a wrong audience or an expired
  token) answers **400**. Getting this backwards is easy and was a real bug during
  development: `MalformedError` subclasses `ValueError`, not `TransportError`.
- **The key set is cached for an hour** (`CachedKeySet`). google-auth fetches it on
  every verification, and on a slow link that is the difference between an instant
  sign-in and a ten-second one. Google's own response says `cache-control: public,
  max-age=24760`, so an hour is conservative. The staleness this accepts is bounded:
  during a key rotation Google keeps publishing the old key for much longer than an
  hour, so the worst case is a refused sign-in that succeeds shortly after. There is
  no refetch-on-failure on purpose — that would let anyone force an outbound request
  per attempt by sending garbage.

## Accounts that already exist

- **An email that already has an account is not adopted.** Google verifies the
  address, and this application stores verified addresses — but linking on that basis
  would make a Google sign-in a way into an account that was created with a password,
  **without the password ever being presented**. That is a takeover path, not a
  convenience, so the sign-in is refused with 409 and a message saying to sign in with
  the username and password. Nothing is written: not the `GOOGLE_SUB`, not the profile.
- **The alternative, if it is ever wanted**: link only after the password has been
  presented once (a "connect Google" action in settings on a signed-in session), which
  keeps the password in the loop. That is a product decision, not a code one, and it is
  not implemented.
- **A Google account whose address is free but whose derived username is taken** simply
  gets the next username (`ali.reza` → `ali.reza2`), and after twenty attempts a short
  random one. The username is not a credential here — Google accounts have no password
  until they set one — so nothing about the choice is security-relevant.

## What a later sign-in must never do

Display name, profile picture, email and every other profile field belong to the user
once the account exists: they can change all of them from settings, and Google holds
older values. So they are used **only when the account is created**; a returning
sign-in writes `last_login_at` and nothing else (`AuthService._issue_session`). The
same rule covers the picture: Google's URL is downloaded, copied into this account's
Cloudinary and stored as `USERS.AVATAR_URL` — never linked to directly, so the
application does not depend on Google continuing to serve it, and never re-fetched for
an account that already has one.

## Two-step verification still applies

An account with `OTP_ENABLED = 1` gets the same code challenge a password login gets
(`otp_required` + `challenge_id`), never a token directly. Google proves *which* Google
account this is; the second factor is this application's own control, and letting one
way in skip it would make it skippable. The frontend already handles that shape, so the
Google button ends in the same code prompt as everything else.

## Accounts created this way have no password

`USERS.PASSWORD_HASH` is nullable, and a Google-created account leaves it NULL.
Password login is refused for such an account — the comparison is against the dummy
hash used for unknown usernames (to spend the same time) and the answer is discarded,
because that dummy's plaintext is a literal in this repository. The account can set a
password from settings, where the current password is not asked for: there is nothing
to prove, and without it the account could never gain one — losing the Google account
would then lose the account. `GET /me` reports `has_password` so the settings form
knows whether to ask for the current one.

## Assumptions and open items

- **The OAuth client must list this application's origins**, and Google is strict
  about what an origin may be: `http://localhost:5173` is registered and works, but an
  **IP address is not accepted** — nor is plain `http` for anything that is not
  localhost. A deployment reached at `http://<ip>` therefore cannot use Google sign-in
  until it has a hostname served over HTTPS. Two ways there: a free HTTPS tunnel
  (Cloudflare Tunnel, ngrok) for a temporary hostname, or any domain pointed at the
  server with a certificate (`nip.io`/`sslip.io` subdomains resolve to the IP and are
  eligible for a Let's Encrypt certificate). Whichever it is, the origin goes in the
  Google console and the same value belongs in the deployed frontend's
  `VITE_GOOGLE_CLIENT_ID` build. Meanwhile the feature degrades cleanly: a frontend
  built without that variable renders no button at all, and the API answers 503.
- **`UK_USERS_GOOGLE_SUB` exists** (added by the project owner, 2026-09-26), so one
  Google account can map to one application account as a database guarantee, not just
  as a convention. Two requests carrying the same fresh credential can still both find
  no account — the constraint is what makes the second insert fail — and that failure
  is answered with 409, with the picture it uploaded along the way removed. The model
  declares the constraint too, so the SQLite test schema enforces what Oracle enforces
  and the race is covered by a test rather than assumed away.
- **Avatar upload happens before the account row is inserted**, so a failed insert has
  to undo it: the upload's `public_id` is kept and deleted on the error path. If the
  Cloudinary *delete* fails it is logged and the account creation error is still
  returned — one failure must not become two.
- **The avatar is best effort.** A picture that cannot be fetched, is not an image, is
  too large, or comes from a host Google does not serve leaves the account without one;
  the UI falls back to the initial. The picture URL is only ever fetched from
  `googleusercontent.com` / `ggpht.com`, and requested at 512x512 (this application's
  avatar master size) by rewriting the size segment of Google's URL.
- **No send-side rate limit on `/auth/google`.** The per-username throttle exists to
  make password guessing expensive; a verified Google credential is not guessable, and
  the only costly path is creating an account, which requires a real Google account.
  Registration is already open and unverified, so this adds no new spam surface.
- **Existing sessions are not revoked by a Google sign-in**, exactly as for password
  logins: tokens are stateless and there is no revocation list in this codebase.
