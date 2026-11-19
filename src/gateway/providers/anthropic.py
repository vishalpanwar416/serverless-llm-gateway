"""Anthropic (messages API)."""

from __future__ import annotations

from typing import Any

from ..config import ProviderSettings
from ..errors import ProviderNotConfiguredError
from ..http import post_json
from ..models import ChatRequest, ProviderResult
from .base import Provider

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 1024  # anthropic needs max_tokens, openai style requests usually don't send it


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, settings: ProviderSettings) -> None:
        self.settings = settings

    def build_payload(self, request: ChatRequest) -> dict[str, Any]:
        # system prompt goes in its own field for anthropic, not in messages
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
            "messages": [m.to_dict() for m in request.messages if m.role != "system"],
        }
        if system:
            payload["system"] = system
        if request.temperature is not None:
            # openai allows 0-2, anthropic only 0-1
            payload["temperature"] = min(request.temperature, 1.0)
        return payload

    def parse_response(self, data: dict[str, Any], request: ChatRequest) -> ProviderResult:
        blocks = self._require(data, "content")
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        usage = data.get("usage") or {}
        return ProviderResult(
            provider=self.name,
            model=data.get("model") or request.model,
            content=text,
            usage=self._usage(usage.get("input_tokens"), usage.get("output_tokens")),
        )

    def complete(self, request: ChatRequest) -> ProviderResult:
        if not self.settings.api_key:
            raise ProviderNotConfiguredError("Provider 'anthropic' is not configured.")
        data = post_json(
            self.name,
            f"{(self.settings.base_url or '').rstrip('/')}/v1/messages",
            self.build_payload(request),
            headers={"x-api-key": self.settings.api_key, "anthropic-version": ANTHROPIC_VERSION},
            timeout_seconds=self.settings.timeout_seconds,
        )
        return self.parse_response(data, request)
