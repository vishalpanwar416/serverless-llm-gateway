from __future__ import annotations

import os
from typing import Any, Callable

import pytest

# handler.py creates a boto3 client on import, so set fake creds before that happens
os.environ.setdefault("AWS_REGION", "ap-southeast-2")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("MOCK_MODE", "true")

from gateway.auth import Authenticator  # noqa: E402
from gateway.config import Settings  # noqa: E402
from gateway.models import ChatRequest, ProviderResult, Usage  # noqa: E402
from gateway.providers.registry import ProviderRegistry  # noqa: E402
from gateway.router import Router  # noqa: E402
from gateway.service import Gateway  # noqa: E402
from gateway.store import ApiKeyRecord, hash_api_key  # noqa: E402

VALID_KEY = "sk-gw-test-key-0001"


class FakeKeyStore:
    def __init__(self, keys: dict[str, ApiKeyRecord] | None = None) -> None:
        self.keys = keys if keys is not None else {
            hash_api_key(VALID_KEY): ApiKeyRecord("key_test", "test", True)
        }
        self.usage: list[dict[str, Any]] = []
        self.fail = False

    def get_api_key(self, key_hash: str) -> ApiKeyRecord | None:
        return self.keys.get(key_hash)

    def record_usage(self, key_hash, *, prompt_tokens, completion_tokens, cache_hit) -> None:
        if self.fail:
            raise RuntimeError("dynamodb down")
        self.usage.append(
            dict(key_hash=key_hash, prompt_tokens=prompt_tokens,
                 completion_tokens=completion_tokens, cache_hit=cache_hit)
        )


class FakeCache:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def get(self, key):
        item = self.items.get(key)
        return dict(item) if item else None

    def put(self, key, response):
        self.items[key] = dict(response)


class ScriptedProvider:
    """Test provider: returns a canned answer or raises whatever error you give it. Remembers its calls."""

    def __init__(self, name: str, behaviour: Callable[[ChatRequest], ProviderResult] | Exception | None = None):
        self.name = name
        self.behaviour = behaviour
        self.calls: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ProviderResult:
        self.calls.append(request)
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        if callable(self.behaviour):
            return self.behaviour(request)
        return ProviderResult(self.name, request.model, f"hello from {self.name}", Usage(10, 5))


def make_settings(**overrides: Any) -> Settings:
    base = dict(
        default_provider="ollama",
        prefix_routes=[("claude-", "anthropic"), ("gpt-", "openai"), ("llama", "ollama")],
        fallback_targets=[("ollama", "llama3.2:1b")],
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def key_store() -> FakeKeyStore:
    return FakeKeyStore()


@pytest.fixture
def cache() -> FakeCache:
    return FakeCache()


@pytest.fixture
def providers() -> dict[str, ScriptedProvider]:
    return {n: ScriptedProvider(n) for n in ("openai", "anthropic", "ollama")}


@pytest.fixture
def gateway(key_store, cache, providers) -> Gateway:
    settings = make_settings()
    return Gateway(
        authenticator=Authenticator(key_store),
        router=Router(ProviderRegistry(providers), settings),
        usage_store=key_store,
        cache=cache,
    )
