"""Decides which provider handles a model, and tries the fallbacks if it fails."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .errors import AllProvidersFailedError, GatewayError
from .jsonlog import log
from .models import ChatRequest, ProviderResult
from .providers.registry import ProviderRegistry


@dataclass(frozen=True)
class Attempt:
    provider: str
    model: str
    error_type: str | None = None  # None means it worked


@dataclass
class RoutedResult:
    result: ProviderResult
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def fallback_used(self) -> bool:
        return len(self.attempts) > 1

    @property
    def primary_provider(self) -> str:
        return self.attempts[0].provider


class Router:
    def __init__(self, registry: ProviderRegistry, settings: Settings) -> None:
        self.registry = registry
        self.settings = settings

    def resolve(self, model: str) -> str:
        """Check MODEL_MAP first, then prefixes, otherwise use the default provider."""
        if model in self.settings.model_map:
            return self.settings.model_map[model]
        for prefix, provider in self.settings.prefix_routes:
            if model.startswith(prefix):
                return provider
        return self.settings.default_provider

    def plan(self, request: ChatRequest) -> list[tuple[str, str]]:
        """Primary target first, then the fallbacks (skipping duplicates)."""
        targets = [(self.resolve(request.model), request.model)]
        for target in self.settings.fallback_targets:
            if target not in targets:
                targets.append(target)
        return targets

    def complete(self, request: ChatRequest, *, request_id: str = "") -> RoutedResult:
        attempts: list[Attempt] = []
        last_error: GatewayError | None = None

        for provider_name, model in self.plan(request):
            try:
                provider = self.registry.get(provider_name)
                result = provider.complete(request.with_model(model))
            except GatewayError as exc:
                if not exc.retryable:
                    # bad request -> don't retry, another provider would reject it too
                    raise
                attempts.append(Attempt(provider_name, model, exc.error_type))
                last_error = exc
                log(
                    "warning",
                    "upstream_attempt_failed",
                    request_id=request_id,
                    target_provider=provider_name,
                    model=model,
                    error_type=exc.error_type,
                    upstream_status=exc.upstream_status,
                )
                continue
            attempts.append(Attempt(provider_name, model))
            return RoutedResult(result=result, attempts=attempts)

        tried = ", ".join(f"{a.provider}({a.error_type})" for a in attempts)
        raise AllProvidersFailedError(
            f"All upstream providers failed: {tried}."
            + (f" Last error: {last_error.message}" if last_error else "")
        )
