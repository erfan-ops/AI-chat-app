# Two-step verification (SMS OTP) — decisions, assumptions, open items

This file records the judgement calls behind the implementation, so that nothing
here is mistaken for "verified behaviour" later.

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
- `otp_enabled = 1` with no stored number **fails closed** (503) rather than falling
  back to password-only login.
- An incorrect or expired code is **400, never 401**: the frontend signs the user out
  on any authenticated 401, so a mistyped code would otherwise eject them.
- The login response for a 2FA account deliberately carries **no mobile hint** —
  revealing the phone suffix to someone holding the password is the attack the
  second factor exists to stop.
- Disabling two-step verification discards any code still in flight.
- Codes are never returned by the API and never logged; the SMS service logs only
  the provider's numeric status and `messageId`.

## Known limitations

- **Enabling 2FA does not revoke existing sessions.** Access tokens are stateless
  JWTs and this codebase has no revocation, so a stolen token keeps working. 2FA
  protects future logins, not sessions already issued.
- **Disabling 2FA does not require the password** (it is an authenticated settings
  action, as specified). Anyone who can act as the user — a stolen token, an XSS —
  can therefore turn the second factor off. Requiring the password here would be a
  reasonable hardening step; it was left out to match the requested behaviour.
- The per-user send cooldown is shared by the enable and login flows, so a user who
  has just enabled 2FA may be asked to wait a minute before the first OTP login.
- `PATCH /me` cannot *clear* `default_model_id` (its schema treats `null` as "not
  provided"), so a default model can be changed but not removed.
- No per-number uniqueness: `MOBILE_NUMBER` has no unique constraint, so two accounts
  can verify the same handset.
