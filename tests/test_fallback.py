"""Routing + fallback. Should fall back on 429/5xx/timeouts but not on 400s."""

from __future__ import annotations

import pytest
import urllib3

from gateway import http
from gateway.errors import (
    AllProvidersFailedError,
    ProviderNotConfiguredError,
    ProviderRequestError,
    UpstreamError,
    UpstreamRateLimitError,
    UpstreamTimeoutError,
)
from gateway.models import ChatRequest
from gateway.providers.registry import ProviderRegistry
from gateway.router import Router

from .conftest import ScriptedProvider, make_settings

REQ = ChatRequest.from_payload({"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]})


def router_with(**behaviours) -> tuple[Router, dict[str, ScriptedProvider]]:
    providers = {n: ScriptedProvider(n, behaviours.get(n)) for n in ("openai", "anthropic", "ollama")}
    return Router(ProviderRegistry(providers), make_settings()), providers


@pytest.mark.parametrize(
    "model,expected",
    [("gpt-4o-mini", "openai"), ("claude-3-5-haiku", "anthropic"), ("llama3.2:1b", "ollama"), ("unknown-x", "ollama")],
)
def test_prefix_routing(model, expected):
    router, _ = router_with()
    assert router.resolve(model) == expected


def test_explicit_model_map_beats_prefix():
    router = Router(ProviderRegistry({}), make_settings(model_map={"gpt-4o-mini": "anthropic"}))
    assert router.resolve("gpt-4o-mini") == "anthropic"


def test_primary_success_does_not_touch_fallback():
    router, p = router_with()
    routed = router.complete(REQ)
    assert routed.result.provider == "openai"
    assert not routed.fallback_used
    assert p["ollama"].calls == []


@pytest.mark.parametrize(
    "error",
    [
        UpstreamRateLimitError("429"),
        UpstreamError("503"),
        UpstreamTimeoutError("timeout"),
        ProviderNotConfiguredError("no key"),
    ],
)
def test_falls_back_on_retryable_errors(error):
    router, p = router_with(openai=error)
    routed = router.complete(REQ)
    assert routed.result.provider == "ollama"
    assert routed.result.model == "llama3.2:1b"  # uses the fallback model, not gpt-4o-mini
    assert routed.fallback_used
    assert [a.provider for a in routed.attempts] == ["openai", "ollama"]


def test_does_not_fall_back_on_client_error():
    router, p = router_with(openai=ProviderRequestError("bad request"))
    with pytest.raises(ProviderRequestError):
        router.complete(REQ)
    assert p["ollama"].calls == []  # fallback should not be called


def test_all_providers_failing_raises_standard_error():
    router, _ = router_with(openai=UpstreamError("500"), ollama=UpstreamTimeoutError("slow"))
    with pytest.raises(AllProvidersFailedError) as exc:
        router.complete(REQ)
    assert "openai(upstream_error)" in exc.value.message
    assert "ollama(upstream_timeout)" in exc.value.message


def test_fallback_target_equal_to_primary_is_not_retried():
    req = ChatRequest.from_payload({"model": "llama3.2:1b", "messages": [{"role": "user", "content": "hi"}]})
    router, p = router_with(ollama=UpstreamError("500"))
    with pytest.raises(AllProvidersFailedError):
        router.complete(req)
    assert len(p["ollama"].calls) == 1


# status code -> error type mapping


@pytest.mark.parametrize(
    "status,error_cls,retryable",
    [
        (429, UpstreamRateLimitError, True),
        (500, UpstreamError, True),
        (502, UpstreamError, True),
        (503, UpstreamError, True),
        (529, UpstreamError, True),  # anthropic uses 529 for overloaded
        (400, ProviderRequestError, False),
        (404, ProviderRequestError, False),
        (422, ProviderRequestError, False),
    ],
)
def test_status_classification(status, error_cls, retryable):
    err = http.classify_status("openai", status, '{"error": {"message": "boom"}}')
    assert isinstance(err, error_cls)
    assert err.retryable is retryable


def test_upstream_read_timeout_becomes_timeout_error(monkeypatch):
    def slow(*args, **kwargs):
        raise urllib3.exceptions.ReadTimeoutError(None, "/", "read timed out")

    monkeypatch.setattr(http._POOL, "request", slow)
    with pytest.raises(UpstreamTimeoutError) as exc:
        http.post_json("openai", "http://x/chat", {}, timeout_seconds=5)
    assert exc.value.retryable


def test_connection_refused_is_retryable(monkeypatch):
    def refused(*args, **kwargs):
        raise urllib3.exceptions.NewConnectionError(None, "refused")

    monkeypatch.setattr(http._POOL, "request", refused)
    with pytest.raises(UpstreamError) as exc:
        http.post_json("ollama", "http://x/api/chat", {}, timeout_seconds=5)
    assert exc.value.retryable
