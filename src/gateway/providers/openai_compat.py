"""OpenAI chat completions.

Also works with anything that copies the OpenAI API (Groq, OpenRouter, vLLM, etc.),
just change OPENAI_BASE_URL.
"""

from __future__ import annotations

from typing import Any

from ..config import ProviderSettings
from ..errors import ProviderNotConfiguredError
from ..http import post_json
from ..models import ChatRequest, ProviderResult
from .base import Provider


class OpenAICompatibleProvider(Provider):
    def __init__(self, name: str, settings: ProviderSettings, *, require_key: bool = True) -> None:
        self.name = name
        self.settings = settings
        self.require_key = require_key

    def build_payload(self, request: ChatRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [m.to_dict() for m in request.messages],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        return payload

    def parse_response(self, data: dict[str, Any], request: ChatRequest) -> ProviderResult:
        content = self._require(data, "choices", 0, "message", "content") or ""
        usage = data.get("usage") or {}
        return ProviderResult(
            provider=self.name,
            model=data.get("model") or request.model,
            content=content,
            usage=self._usage(usage.get("prompt_tokens"), usage.get("completion_tokens")),
        )

    def complete(self, request: ChatRequest) -> ProviderResult:
        if not self.settings.base_url or (self.require_key and not self.settings.api_key):
            raise ProviderNotConfiguredError(f"Provider '{self.name}' is not configured.")
        headers = {}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        data = post_json(
            self.name,
            f"{self.settings.base_url.rstrip('/')}/chat/completions",
            self.build_payload(request),
            headers=headers,
            timeout_seconds=self.settings.timeout_seconds,
        )
        return self.parse_response(data, request)
