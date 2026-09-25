/**
 * Domain types mirroring the backend's Pydantic schemas one-to-one.
 * Reference: docs/openapi.json (snapshot of http://localhost:8000/openapi.json).
 *
 * All ids are database integers; timestamps are naive-UTC ISO strings
 * (the backend stores Oracle TIMESTAMP values as UTC without a zone suffix),
 * so always parse them with {@link parseIsoUtc} from utils/dates.ts.
 */

/** Public user profile (POST /auth/register, GET /me, login response). */
export interface User {
  id: number
  username: string
  display_name: string | null
  role: string
  status: string
  default_model_id: number | null
  created_at: string
  last_login_at: string | null
  /** Verified mobile as the canonical 10-digit local form (e.g. "9123456789");
   *  the "+98" form is presentation only. */
  mobile_number: string | null
  /** Verified email address, or null when none has been confirmed yet. */
  email: string | null
  /** True once two-step verification is active — a code is required at login. */
  otp_enabled: boolean
  /** Channel login codes go to by default; a login may temporarily use the other. */
  preferred_otp_method: OtpMethod
  /** Whether an authenticator app is enrolled, so the method can be offered.
   *  The answer only — the server never sends the secret itself. */
  authenticator_enrolled: boolean
  /** Profile picture: the Cloudinary master the client uploaded, resized at
   *  delivery (see utils/cloudinary.ts). Null when none has been set. */
  avatar_url: string | null
}

export interface RegisterRequest {
  username: string
  password: string
  display_name?: string | null
}

export interface LoginRequest {
  username: string
  password: string
}

/** Successful login: a bearer access token plus the user profile. */
export interface LoginResponse {
  access_token: string
  token_type: 'bearer'
  /** Token lifetime in minutes. */
  expires_in: number
  user: User
}

/** How a verification code reaches the user: one we send, or one their own
 *  authenticator app generates. */
export type OtpMethod = 'SMS' | 'EMAIL' | 'TOTP'

/** The methods a code is *sent* through. TOTP sends nothing, so it has no
 *  destination to collect or mask. */
export type SentOtpMethod = 'SMS' | 'EMAIL'

/** Returned by POST /auth/login when the account has two-step verification on:
 *  the password was right, but no token exists until the code is verified.
 *  Names the channel the code went to but never the contact itself — a password
 *  holder must not learn the number or address behind it. */
export interface OtpRequiredResponse {
  otp_required: true
  challenge_id: string
  /** Code lifetime in seconds (not minutes — see LoginResponse.expires_in). */
  code_expires_in_seconds: number
  /** Channel the code was sent through. */
  delivery_method: OtpMethod
  /** The other channel, present only when it can actually be used. */
  alternative_method: OtpMethod | null
}

/** A code was sent; the challenge id ties the next call to this attempt. */
export interface OtpChallenge {
  challenge_id: string
  code_expires_in_seconds: number
  /** Channel the code was sent through — never TOTP, which sends nothing. */
  method: SentOtpMethod
  /** Masked address the code was sent to, e.g. "+98 912 *** 6789" or
   *  "al***@example.com". Shown only where the user just typed it. */
  destination_hint: string
}

/** Body for POST /me/otp/enable — the contact to verify, by SMS or by email. */
export interface OtpEnableRequest {
  method: SentOtpMethod
  /** 10 digits without the +98 prefix; required when method is "SMS". */
  mobile_number?: string
  email?: string
}

/** Body for POST /auth/login/otp/method — resend this login's code the other way. */
export interface LoginOtpMethodRequest {
  challenge_id: string
  method: OtpMethod
}

/** Returned by POST /me/totp/enable: an authenticator has been provisioned, and
 *  a code from it is needed to finish. Two-step verification is *not* on yet.
 *  Both values are shown once, to the signed-in owner, and are never stored. */
export interface TotpEnrollment {
  challenge_id: string
  /** Base32 secret, for entering into the app by hand. */
  secret: string
  /** otpauth:// URI the QR code encodes. */
  otpauth_uri: string
  code_expires_in_seconds: number
}

export interface OtpVerifyRequest {
  challenge_id: string
  code: string
}

export interface PasswordChangeRequest {
  current_password: string
  /** Same rules as registration: at least 8 characters. */
  new_password: string
}

export interface UserUpdate {
  username?: string | null
  display_name?: string | null
  default_model_id?: number | null
  preferred_otp_method?: OtpMethod
  /** The uploaded Cloudinary URL, or null to remove the picture. Omitting the field
   *  leaves it alone — which is why the form only sends it when it changed. */
  avatar_url?: string | null
}

/** An AI character you chat with (mirrors CharacterRead). `system_prompt` is
 *  the persona prompt sent to the AI provider; `description` is a short
 *  catalog blurb and is not part of the prompt. `owner_user_id` is null for
 *  built-in characters, the creator's id for private ones. */
export interface Character {
  id: number
  name: string
  description: string | null
  avatar_url: string | null
  system_prompt: string | null
  status: string
  created_at: string
  owner_user_id: number | null
}

/** What an upload is for. The server maps it to a Cloudinary folder, so the client
 *  names a kind rather than a path. */
export type UploadKind = 'character' | 'user'

/** Body for POST /cloudinary/signature — optional, and a character avatar by
 *  default, which is what this endpoint signed before profile pictures existed. */
export interface UploadSignatureRequest {
  kind: UploadKind
}

/** Response for POST /cloudinary/signature — everything the browser needs to
 *  upload an avatar straight to Cloudinary. The API secret is never returned.
 *  `folder`, `timestamp` and `signature` must be sent back verbatim. */
export interface UploadSignature {
  signature: string
  timestamp: number
  api_key: string
  cloud_name: string
  folder: string
}

/** Body for POST /characters. Only `name` is required; the rest are optional
 *  and stored as null when omitted. `owner_user_id` and `status` are
 *  administrator-only and must never be sent by this app (the owner always
 *  comes from the access token). */
export interface CharacterCreateRequest {
  name: string
  description?: string | null
  avatar_url?: string | null
  system_prompt?: string | null
}

/** A user-authored persona — how the user presents themselves in a chat
 *  (mirrors PersonaRead; the AI character above is a separate concept).
 *  Only the fields the user filled in are set, the rest are null. */
export interface Persona {
  id: number
  user_id: number
  name: string
  gender: string | null
  description: string | null
  age: number | null
}

/** Body for POST /personas. Only `name` is required; the rest are optional
 *  and stored as null when omitted. */
export interface PersonaCreateRequest {
  name: string
  gender?: string | null
  description?: string | null
  age?: number | null
}

/** An active AI model offered for conversations (API keys are never exposed). */
export interface AIModel {
  id: number
  model_name: string
  display_name: string | null
  provider: string | null
  context_window: number | null
}

export interface Conversation {
  id: number
  character: { id: number; name: string } | null
  model: { id: number; model_name: string; display_name: string | null } | null
  /** The user's chosen persona for this chat; null when none was picked. */
  user_persona: { id: number; name: string } | null
  title: string | null
  status: string
  created_at: string
  updated_at: string
  last_message_at: string | null
}

/** Body for POST /conversations. `model_id` defaults server-side to the
 *  user's default model, then to the first active model. `user_persona_id`
 *  is optional — omit it (or pass null) to chat without a persona. */
export interface ConversationCreateRequest {
  character_id: number
  model_id?: number | null
  user_persona_id?: number | null
  title?: string | null
}

/** Body for PATCH /conversations/{id}. */
export interface ConversationUpdateRequest {
  title: string
}

export type MessageRole = 'user' | 'assistant'

export interface Message {
  id: number
  conversation_id: number
  role: MessageRole
  content: string
  created_at: string
  /** Id of the message this one replies to; null when it is not a reply.
   *  Both user and assistant messages can carry one — the AI sets it in its
   *  structured reply when it answers an earlier message. */
  reply_to_id: number | null
}

/** Body for POST /conversations/{id}/messages (starts a streamed AI reply).
 *  `reply_to_id` must reference a message in the same conversation (404 otherwise). */
export interface MessageCreateRequest {
  /** The sender's IANA timezone name, e.g. "Asia/Tehran". Optional. */
  client_timezone?: string
  /** Minutes east of UTC, e.g. 210 for +03:30. Optional. */
  client_utc_offset_minutes?: number
  content: string
  reply_to_id?: number | null
}

/* --- Server-Sent Events payloads ----------------------------------------------
 * POST /conversations/{id}/messages streams:
 *   message.created → message.delta* → message.completed, or error. */

/** `message.created` — the persisted user message. */
export interface MessageCreatedEvent {
  conversation_id: number
  message: Message
}

/** `message.delta` — one incremental piece of the AI reply. */
export interface MessageDeltaEvent {
  content: string
}

export interface UsageData {
  input_tokens: number
  output_tokens: number
  total_tokens: number
}

/** `message.completed` — the fully persisted assistant message + telemetry. */
export interface MessageCompletedEvent {
  message: Message
  usage: UsageData | null
  latency_ms: number | null
}

/** `error` — the stream failed; no assistant message was persisted. */
export interface StreamErrorEvent {
  code: string
  detail: string
}
