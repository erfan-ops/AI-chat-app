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

export interface UserUpdate {
  display_name?: string | null
  default_model_id?: number | null
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
