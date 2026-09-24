"""Message schemas, including the Server-Sent Events payloads used for streaming."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MessageCreate(BaseModel):
    """Body for POST /conversations/{id}/messages (starts a streamed AI reply).

    ``client_timezone`` / ``client_utc_offset_minutes`` describe the *sender's* clock
    (the browser knows both), so the model can be told what time it is where the user
    is. They are optional: without them the AI sees UTC timestamps only, exactly as
    before. Both are treated as untrusted text — see app/ai/context.py, which strips
    anything unusable rather than failing the message over it.
    """

    content: str = Field(min_length=1, max_length=32000)
    reply_to_id: int | None = Field(default=None, ge=1)
    client_timezone: str | None = Field(default=None, max_length=64)
    # UTC offsets run from -12:00 to +14:00; anything outside is not a real zone.
    client_utc_offset_minutes: int | None = Field(default=None, ge=-840, le=840)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: str
    content: str
    created_at: datetime
    reply_to_id: int | None


# --- Server-Sent Events payloads -------------------------------------------------


class UsageData(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class MessageCreatedPayload(BaseModel):
    """SSE ``message.created`` — the persisted user message."""

    conversation_id: int
    message: MessageRead


class MessageDeltaPayload(BaseModel):
    """SSE ``message.delta`` — one incremental piece of the AI reply."""

    content: str


class MessageCompletedPayload(BaseModel):
    """SSE ``message.completed`` — the fully persisted assistant message."""

    message: MessageRead
    usage: UsageData | None
    latency_ms: int | None


class StreamErrorPayload(BaseModel):
    """SSE ``error`` — the stream failed; no assistant message was persisted."""

    code: str
    detail: str
