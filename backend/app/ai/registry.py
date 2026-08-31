"""Provider factory: maps a resolved EndpointConfig to a provider implementation.

Unknown provider names fall back to the OpenAI-compatible implementation, which
also covers DeepSeek and OpenAI-compatible custom gateways.
"""

from __future__ import annotations

import httpx

from app.ai.base import AIProvider, EndpointConfig
from app.ai.providers.anthropic import AnthropicProvider
from app.ai.providers.mock import MockProvider
from app.ai.providers.openai import OpenAICompatibleProvider

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"


def create_provider(endpoint: EndpointConfig, timeout: httpx.Timeout) -> AIProvider:
    """Create the provider implementation for a resolved endpoint configuration."""
    name = endpoint.provider_name.strip().lower()
    if name == "mock":
        return MockProvider()
    if name == "anthropic":
        return AnthropicProvider(api_key=endpoint.api_key, timeout=timeout)
    return OpenAICompatibleProvider(
        base_url=endpoint.base_url or OPENAI_DEFAULT_BASE_URL,
        api_key=endpoint.api_key,
        timeout=timeout,
    )
