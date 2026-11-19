"""Checks that each provider's response gets converted to the same format."""

from __future__ import annotations

import pytest

from gateway.config import ProviderSettings
from gateway.errors import ProviderNotConfiguredError, UpstreamError
from gateway.models import ChatRequest, ChatResponse, Usage
from gateway.providers.anthropic import AnthropicProvider
from gateway.providers.mock import MockProvider
from gateway.providers.ollama import OllamaProvider
from gateway.providers.openai_compat import OpenAICompatibleProvider

REQ = ChatRequest.from_payload(
    {
        "model": "some-model",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Explain serverless cold starts in one sentence."},
        ],
        "temperature": 0.7,
    }
)
SETTINGS = ProviderSettings(base_url="http://upstream", api_key="k", timeout_seconds=5)


def test_openai_response_normalized():
    raw = {
        "id": "chatcmpl-1",
        "model": "gpt-4o-mini-2024-07-18",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi!"}}],
        "usage": {"prompt_tokens": 25, "completion_tokens": 18, "total_tokens": 43},
    }
    result = OpenAICompatibleProvider("openai", SETTINGS).parse_response(raw, REQ)
    assert (result.provider, result.model, result.content) == ("openai", "gpt-4o-mini-2024-07-18", "Hi!")
    assert result.usage == Usage(25, 18)


def test_anthropic_response_normalized():
    raw = {
        "model": "claude-3-5-haiku-latest",
        "content": [{"type": "text", "text": "Cold "}, {"type": "text", "text": "starts."}],
        "usage": {"input_tokens": 30, "output_tokens": 7},
    }
    result = AnthropicProvider(SETTINGS).parse_response(raw, REQ)
    assert result.content == "Cold starts."
    assert result.usage == Usage(30, 7)
    assert result.provider == "anthropic"


def test_anthropic_payload_moves_system_prompt_and_clamps_temperature():
    req = ChatRequest.from_payload({**_payload(), "temperature": 1.8})
    payload = AnthropicProvider(SETTINGS).build_payload(req)
    assert payload["system"] == "You are a helpful assistant."
    assert all(m["role"] != "system" for m in payload["messages"])
    assert payload["max_tokens"] == 1024
    assert payload["temperature"] == 1.0


def test_ollama_response_normalized():
    raw = {
        "model": "llama3.2:1b",
        "message": {"role": "assistant", "content": "A cold start is..."},
        "prompt_eval_count": 31,
        "eval_count": 12,
        "done": True,
    }
    result = OllamaProvider(SETTINGS).parse_response(raw, REQ)
    assert result.usage == Usage(31, 12)
    assert result.content == "A cold start is..."


def test_ollama_payload_maps_options():
    req = ChatRequest.from_payload({**_payload(), "temperature": 0, "max_tokens": 50})
    payload = OllamaProvider(SETTINGS).build_payload(req)
    assert payload["stream"] is False
    assert payload["options"] == {"temperature": 0.0, "num_predict": 50}


def test_malformed_upstream_response_is_upstream_error():
    with pytest.raises(UpstreamError):
        OpenAICompatibleProvider("openai", SETTINGS).parse_response({"choices": []}, REQ)


def test_missing_credentials_is_retryable_not_configured():
    provider = OpenAICompatibleProvider("openai", ProviderSettings(base_url="http://x"))
    with pytest.raises(ProviderNotConfiguredError) as exc:
        provider.complete(REQ)
    assert exc.value.retryable


def test_mock_provider_returns_realistic_usage():
    result = MockProvider("openai", sleep=lambda _: None).complete(REQ)
    assert result.provider == "openai"
    assert result.usage.prompt_tokens > 0 and result.usage.completion_tokens > 0


def test_unified_response_shape():
    body = ChatResponse(
        id="req_1", provider="openai", model="gpt-4o-mini", content="x",
        usage=Usage(25, 18), latency_ms=412,
    ).to_dict()
    assert body["usage"] == {"prompt_tokens": 25, "completion_tokens": 18, "total_tokens": 43}
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "x"}
    assert {"id", "provider", "model", "choices", "usage", "latency_ms", "cached"} <= body.keys()


def _payload():
    return {
        "model": "m",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "hi"},
        ],
    }
