"""Settings, all read from env vars (routing, fallbacks, timeouts, mock mode, table names)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Mapping


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_prefix_routes(raw: str) -> list[tuple[str, str]]:
    """Parses "gpt-=openai,claude-=anthropic" into a list of (prefix, provider).

    Longest prefix goes first so a more specific rule wins over a general one.
    """
    routes: list[tuple[str, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        prefix, sep, provider = part.partition("=")
        if not sep or not provider.strip():
            raise ValueError(f"Invalid MODEL_PREFIX_ROUTES entry: {part!r}")
        routes.append((prefix.strip(), provider.strip()))
    return sorted(routes, key=lambda r: len(r[0]), reverse=True)


def _parse_targets(raw: str) -> list[tuple[str, str]]:
    """Parses "ollama:llama3.2:1b,openai:gpt-4o-mini" into (provider, model) pairs.

    Only split on the first colon, since ollama model names have colons in them too.
    """
    targets: list[tuple[str, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        provider, sep, model = part.partition(":")
        if not sep or not model:
            raise ValueError(f"Invalid FALLBACK_TARGETS entry (want provider:model): {part!r}")
        targets.append((provider.strip(), model.strip()))
    return targets


@dataclass(frozen=True)
class ProviderSettings:
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: float = 5.0


@dataclass(frozen=True)
class Settings:
    mock_mode: bool = False
    default_provider: str = "ollama"
    model_map: Mapping[str, str] = field(default_factory=dict)
    prefix_routes: list[tuple[str, str]] = field(default_factory=list)
    fallback_targets: list[tuple[str, str]] = field(default_factory=list)
    providers: Mapping[str, ProviderSettings] = field(default_factory=dict)

    api_keys_table: str = "llm-gateway-api-keys"
    cache_table: str = "llm-gateway-cache"
    cache_enabled: bool = True
    cache_ttl_seconds: int = 3600
    dynamodb_endpoint: str | None = None
    aws_region: str = "ap-southeast-2"

    mock_latency_ms: tuple[int, int] = (120, 450)
    mock_failures: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        default_timeout = float(e.get("UPSTREAM_TIMEOUT_SECONDS", "5"))

        def provider(name: str, default_url: str | None) -> ProviderSettings:
            upper = name.upper()
            return ProviderSettings(
                base_url=e.get(f"{upper}_BASE_URL", default_url),
                api_key=e.get(f"{upper}_API_KEY") or None,
                timeout_seconds=float(e.get(f"{upper}_TIMEOUT_SECONDS", default_timeout)),
            )

        lo, _, hi = e.get("MOCK_LATENCY_MS", "120-450").partition("-")
        mock_failures = {}
        for part in e.get("MOCK_FAILURES", "").split(","):
            name, sep, kind = part.strip().partition("=")
            if sep:
                mock_failures[name.strip()] = kind.strip()

        return cls(
            mock_mode=_bool(e.get("MOCK_MODE"), default=False),
            default_provider=e.get("DEFAULT_PROVIDER", "ollama"),
            model_map=json.loads(e.get("MODEL_MAP", "{}") or "{}"),
            prefix_routes=_parse_prefix_routes(
                e.get(
                    "MODEL_PREFIX_ROUTES",
                    "gpt-=openai,o1=openai,o3=openai,claude-=anthropic,"
                    "llama=ollama,qwen=ollama,mistral=ollama,gemma=ollama,mock-=mock",
                )
            ),
            fallback_targets=_parse_targets(e.get("FALLBACK_TARGETS", "ollama:llama3.2:1b")),
            providers={
                "openai": provider("openai", "https://api.openai.com/v1"),
                "anthropic": provider("anthropic", "https://api.anthropic.com"),
                "ollama": provider("ollama", "http://localhost:11434"),
            },
            api_keys_table=e.get("API_KEYS_TABLE", "llm-gateway-api-keys"),
            cache_table=e.get("CACHE_TABLE", "llm-gateway-cache"),
            cache_enabled=_bool(e.get("CACHE_ENABLED"), default=True),
            cache_ttl_seconds=int(e.get("CACHE_TTL_SECONDS", "3600")),
            dynamodb_endpoint=e.get("DYNAMODB_ENDPOINT") or None,
            aws_region=e.get("AWS_REGION", e.get("AWS_DEFAULT_REGION", "ap-southeast-2")),
            mock_latency_ms=(int(lo), int(hi or lo)),
            mock_failures=mock_failures,
        )
