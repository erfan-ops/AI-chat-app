"""Provider-independent types shared by the AI layer and the rest of the app.

The rest of the application only ever sees ``AIProvider.stream_chat`` — no
provider-specific classes leak past this package.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class ChatRequest:
    """A normalized model request: system prompt + chronological history."""

    model: str
    messages: Sequence[ChatMessage]
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class DeltaEvent:
    """One incremental piece of the reply."""

    content: str


@dataclass(frozen=True)
class CompletionEvent:
    """The stream finished; carries the complete reply."""

    content: str
    usage: Usage | None = None


@dataclass(frozen=True)
class ErrorEvent:
    """The provider failed; no reply was produced."""

    message: str
    retryable: bool = False


StreamEvent = DeltaEvent | CompletionEvent | ErrorEvent


@dataclass(frozen=True)
class EndpointConfig:
    """Resolved target for one generation (from the DB or settings override)."""

    provider_name: str
    base_url: str
    api_key: str
    model: str


@runtime_checkable
class AIProvider(Protocol):
    """Streaming chat provider protocol.

    Implemented by async-generator functions that yield ``DeltaEvent`` chunks
    as they arrive, then a ``CompletionEvent`` (or ``ErrorEvent`` on failure).
    Consumers cancel a stream by calling ``aclose()`` on the returned generator
    — implementations must release their HTTP stream in a ``finally`` block.
    """

    def stream_chat(self, request: ChatRequest) -> AsyncGenerator[StreamEvent]: ...
