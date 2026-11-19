"""Ollama (/api/chat). Free, runs locally."""

from __future__ import annotations

from typing import Any

from ..config import ProviderSettings
from ..errors import ProviderNotConfiguredError
from ..http import post_json
from ..models import ChatRequest, ProviderResult
from .base import Provider


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, settings: ProviderSettings) -> None:
        self.settings = settings

    def build_payload(self, request: ChatRequest) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [m.to_dict() for m in request.messages],
            "stream": False,
        }
        if options:
            payload["options"] = options
        return payload

    def parse_response(self, data: dict[str, Any], request: ChatRequest) -> ProviderResult:
        content = self._require(data, "message", "content") or ""
        return ProviderResult(
            provider=self.name,
            model=data.get("model") or request.model,
            content=content,
            usage=self._usage(data.get("prompt_eval_count"), data.get("eval_count")),
        )

    def complete(self, request: ChatRequest) -> ProviderResult:
        if not self.settings.base_url:
            raise ProviderNotConfiguredError("Provider 'ollama' is not configured.")
        headers = {}
        if self.settings.api_key:  # if ollama is behind a proxy that wants a token
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        data = post_json(
            self.name,
            f"{self.settings.base_url.rstrip('/')}/api/chat",
            self.build_payload(request),
            headers=headers,
            timeout_seconds=self.settings.timeout_seconds,
        )
        return self.parse_response(data, request)
