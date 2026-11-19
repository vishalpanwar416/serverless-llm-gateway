"""Creates all the providers. This is the only file that needs to know about each one."""

from __future__ import annotations

from typing import Callable, Mapping, Protocol

from ..config import ProviderSettings, Settings
from ..errors import ProviderNotConfiguredError
from ..models import ChatRequest, ProviderResult
from .anthropic import AnthropicProvider
from .mock import MockProvider
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatibleProvider


class SupportsComplete(Protocol):
    name: str

    def complete(self, request: ChatRequest) -> ProviderResult: ...


# new provider? add it here
FACTORIES: dict[str, Callable[[ProviderSettings], SupportsComplete]] = {
    "openai": lambda s: OpenAICompatibleProvider("openai", s),
    "anthropic": AnthropicProvider,
    "ollama": OllamaProvider,
}


class ProviderRegistry:
    def __init__(self, providers: Mapping[str, SupportsComplete]) -> None:
        self._providers = dict(providers)

    def get(self, name: str) -> SupportsComplete:
        try:
            return self._providers[name]
        except KeyError:
            # unknown provider name = treat it as unavailable so we fall back
            raise ProviderNotConfiguredError(f"Provider '{name}' is not registered.") from None

    def names(self) -> list[str]:
        return sorted(self._providers)


def build_registry(settings: Settings) -> ProviderRegistry:
    names = set(FACTORIES) | {"mock"}
    if settings.mock_mode:
        # each provider gets a mock with its own name, so routing/fallback still
        # work the same as with real keys
        providers = {
            n: MockProvider(
                n, latency_ms=settings.mock_latency_ms, failure=settings.mock_failures.get(n)
            )
            for n in names
        }
    else:
        providers = {
            n: factory(settings.providers.get(n, ProviderSettings()))
            for n, factory in FACTORIES.items()
        }
        providers["mock"] = MockProvider(
            "mock", latency_ms=settings.mock_latency_ms, failure=settings.mock_failures.get("mock")
        )
    return ProviderRegistry(providers)
