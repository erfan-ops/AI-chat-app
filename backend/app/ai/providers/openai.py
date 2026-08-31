"""OpenAI-compatible chat-completions streaming provider.

Covers OpenAI, DeepSeek, and OpenAI-compatible custom gateways. Uses a raw
``httpx`` async stream (no vendor SDK) and parses the SSE chunks incrementally.
"""

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

_SSE_DATA_PREFIX = "data:"


def _parse_usage(raw: object) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    input_tokens = raw.get("input_tokens") or raw.get("prompt_tokens")
    output_tokens = raw.get("output_tokens") or raw.get("completion_tokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return None
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens)


class OpenAICompatibleProvider:
    def __init__(self, *, base_url: str, api_key: str, timeout: httpx.Timeout) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    @property
    def _url(self) -> str:
        return f"{self._base_url}/chat/completions"

    async def stream_chat(self, request: ChatRequest) -> AsyncGenerator[StreamEvent]:
        body = {
            "model": request.model,
            "messages": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],
            "stream": True,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            # Ask the provider to report usage in the final chunk.
            "stream_options": {"include_usage": True},
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                stream = client.stream("POST", self._url, json=body, headers=headers)
                async with stream as response:
                    if response.status_code != 200:
                        yield ErrorEvent(
                            message=f"AI provider returned HTTP {response.status_code}",
                            retryable=response.status_code >= 500,
                        )
                        return
                    content: list[str] = []
                    usage: Usage | None = None
                    async for line in response.aiter_lines():
                        if not line.startswith(_SSE_DATA_PREFIX):
                            continue
                        data = line[len(_SSE_DATA_PREFIX) :].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = cast(dict[str, Any], json.loads(data))
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("usage"):
                            usage = _parse_usage(chunk["usage"])
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            content.append(piece)
                            yield DeltaEvent(content=piece)
                    full = "".join(content)
                    if full:
                        yield CompletionEvent(content=full, usage=usage)
                    else:
                        yield ErrorEvent(message="AI provider returned an empty response")
        except httpx.HTTPError:
            yield ErrorEvent(message="Failed to reach the AI provider", retryable=True)
