"""Anthropic Messages API streaming provider (raw HTTP SSE, no vendor SDK)."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any, cast

import httpx

from app.ai.base import (
    ChatRequest,
    CompletionEvent,
    DeltaEvent,
    ErrorEvent,
    StreamEvent,
    Usage,
)

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
_SSE_PREFIX = ("event:", "data:")


class AnthropicProvider:
    def __init__(self, *, api_key: str, timeout: httpx.Timeout) -> None:
        self._api_key = api_key
        self._timeout = timeout

    async def stream_chat(self, request: ChatRequest) -> AsyncGenerator[StreamEvent]:
        system_parts = [message.content for message in request.messages if message.role == "system"]
        chat_messages = [message for message in request.messages if message.role != "system"]
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": message.role, "content": message.content} for message in chat_messages
            ],
            "max_tokens": request.max_tokens,
            # Anthropic clamps temperature to [0, 1].
            "temperature": min(request.temperature, 1.0),
            "stream": True,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        headers = {"x-api-key": self._api_key, "anthropic-version": ANTHROPIC_VERSION}
        url = f"{ANTHROPIC_BASE_URL}/messages"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                stream = client.stream("POST", url, json=body, headers=headers)
                async with stream as response:
                    if response.status_code != 200:
                        yield ErrorEvent(
                            message=f"AI provider returned HTTP {response.status_code}",
                            retryable=response.status_code >= 500,
                        )
                        return
                    content: list[str] = []
                    input_tokens = 0
                    output_tokens = 0
                    event_name: str | None = None
                    async for line in response.aiter_lines():
                        if not line.startswith(_SSE_PREFIX):
                            continue
                        if line.startswith("event:"):
                            event_name = line[len("event:") :].strip()
                            continue
                        data = line[len("data:") :].strip()
                        try:
                            payload = cast(dict[str, Any], json.loads(data))
                        except json.JSONDecodeError:
                            continue
                        if event_name == "content_block_delta":
                            delta = payload.get("delta") or {}
                            piece = delta.get("text")
                            if piece:
                                content.append(piece)
                                yield DeltaEvent(content=piece)
                        elif event_name == "message_start":
                            usage = (payload.get("message") or {}).get("usage") or {}
                            if isinstance(usage.get("input_tokens"), int):
                                input_tokens = usage["input_tokens"]
                        elif event_name == "message_delta":
                            usage = payload.get("usage") or {}
                            if isinstance(usage.get("output_tokens"), int):
                                output_tokens = usage["output_tokens"]
                            if isinstance(usage.get("input_tokens"), int):
                                input_tokens = usage["input_tokens"]
                        elif event_name == "message_stop":
                            break
                        elif event_name == "error":
                            error = payload.get("error") or {}
                            yield ErrorEvent(
                                message=str(error.get("message") or "AI provider error")
                            )
                            return
                    full = "".join(content)
                    usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens)
                    if full:
                        yield CompletionEvent(content=full, usage=usage)
                    else:
                        yield ErrorEvent(message="AI provider returned an empty response")
        except httpx.HTTPError:
            yield ErrorEvent(message="Failed to reach the AI provider", retryable=True)
