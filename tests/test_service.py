"""Tests the whole request flow, using fake stores instead of DynamoDB."""

from __future__ import annotations

import json

import pytest

from gateway.errors import ProviderRequestError, UpstreamRateLimitError

from .conftest import VALID_KEY

AUTH = {"authorization": f"Bearer {VALID_KEY}"}
BODY = {
    "model": "gpt-4o-mini",
    "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Explain serverless cold starts in one sentence."},
    ],
    "temperature": 0.7,
}


def call(gateway, body=BODY, headers=AUTH, method="POST", path="/v1/chat/completions"):
    raw = body if isinstance(body, str) or body is None else json.dumps(body)
    return gateway.handle(method=method, path=path, headers=headers, body=raw, request_id="req_test")


def test_success_returns_unified_response_and_tracks_usage(gateway, key_store):
    resp = call(gateway)
    assert resp.status_code == 200
    assert resp.body["provider"] == "openai"
    assert resp.body["cached"] is False
    assert resp.body["usage"]["total_tokens"] == 15
    assert resp.body["id"] == "req_test"
    assert key_store.usage == [
        {"key_hash": key_store.usage[0]["key_hash"], "prompt_tokens": 10, "completion_tokens": 5, "cache_hit": False}
    ]


def test_missing_auth_is_401_and_never_calls_provider(gateway, providers):
    resp = call(gateway, headers={})
    assert resp.status_code == 401
    assert resp.body["error"]["type"] == "authentication_error"
    assert providers["openai"].calls == []


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        {"messages": [{"role": "user", "content": "hi"}]},
        {"model": "gpt-4o-mini", "messages": []},
        {"model": "gpt-4o-mini", "messages": [{"role": "wizard", "content": "hi"}]},
        {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}], "temperature": 5},
    ],
)
def test_invalid_payload_is_400_without_any_upstream_call(gateway, providers, body):
    resp = call(gateway, body=body)
    assert resp.status_code == 400
    assert resp.body["error"]["type"] == "invalid_request_error"
    assert all(p.calls == [] for p in providers.values())


def test_fallback_is_visible_in_headers(gateway, providers):
    providers["openai"].behaviour = UpstreamRateLimitError("429")
    resp = call(gateway)
    assert resp.status_code == 200
    assert resp.body["provider"] == "ollama"
    assert resp.headers["x-gateway-fallback"] == "openai->ollama"


def test_upstream_400_is_returned_not_retried(gateway, providers):
    providers["openai"].behaviour = ProviderRequestError("context length exceeded")
    resp = call(gateway)
    assert resp.status_code == 400
    assert providers["ollama"].calls == []


def test_error_payload_has_no_internal_details(gateway, providers):
    providers["openai"].behaviour = RuntimeError("secret stack detail")
    resp = call(gateway)
    assert resp.status_code == 500
    assert "secret" not in json.dumps(resp.body)


def test_deterministic_requests_are_cached(gateway, providers, key_store):
    body = {**BODY, "temperature": 0}
    first = call(gateway, body=body)
    second = call(gateway, body=body)
    assert first.body["cached"] is False and second.body["cached"] is True
    assert second.body["choices"] == first.body["choices"]
    assert len(providers["openai"].calls) == 1
    # cache hit still counts as a request, but with 0 tokens
    assert key_store.usage[-1]["cache_hit"] is True
    assert key_store.usage[-1]["prompt_tokens"] == 0


def test_non_deterministic_requests_are_not_cached(gateway, providers):
    call(gateway)
    call(gateway)
    assert len(providers["openai"].calls) == 2


def test_usage_store_outage_does_not_fail_the_request(gateway, key_store):
    key_store.fail = True
    assert call(gateway).status_code == 200


def test_routes_and_methods(gateway):
    assert call(gateway, path="/health", method="GET", body=None).status_code == 200
    assert call(gateway, path="/nope").status_code == 404
    assert call(gateway, method="GET", body=None).status_code == 405


def test_logs_are_json_and_never_contain_key_or_prompt(gateway, capsys):
    call(gateway)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    final = lines[-1]
    assert final["message"] == "request_completed"
    for field in ("request_id", "api_key_id", "target_provider", "status_code", "latency_ms"):
        assert field in final
    text = json.dumps(lines)
    assert VALID_KEY not in text
    assert "cold starts" not in text
