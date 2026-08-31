"""Deterministic in-process provider for development (AI_PROVIDER=mock)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from app.ai.base import ChatRequest, CompletionEvent, DeltaEvent, StreamEvent, Usage

MOCK_REPLY = "I hear you! Thanks for telling me — I'm always here for you. 💕"


class MockProvider:
    def __init__(self, *, chunk_size: int = 6, delay_seconds: float = 0.02) -> None:
        self._chunk_size = chunk_size
        self._delay_seconds = delay_seconds

    async def stream_chat(self, request: ChatRequest) -> AsyncGenerator[StreamEvent]:
        try:
            pieces = [
                MOCK_REPLY[i : i + self._chunk_size]
                for i in range(0, len(MOCK_REPLY), self._chunk_size)
            ]
            for piece in pieces:
                if self._delay_seconds:
                    await asyncio.sleep(self._delay_seconds)
                yield DeltaEvent(content=piece)
            yield CompletionEvent(
                content=MOCK_REPLY,
                usage=Usage(
                    input_tokens=len(request.messages) * 3, output_tokens=len(MOCK_REPLY) // 4
                ),
            )
        finally:
            # No HTTP resources to release; the finally documents the contract that
            # consumers may cancel the stream at any time.
            pass
