"""Fake provider used when MOCK_MODE=true. Adds some latency and rough token counts.

You can make a provider fail on purpose to test fallback, e.g.
MOCK_FAILURES="openai=429,anthropic=timeout" (also 500, 503, 400).
"""

from __future__ import annotations

import hashlib
import random
import time

from ..errors import (
    ProviderRequestError,
    UpstreamError,
    UpstreamRateLimitError,
    UpstreamTimeoutError,
)
from ..models import ChatRequest, ProviderResult, Usage

_REPLIES = (
    "Serverless cold starts happen when a new execution environment must be created "
    "and initialized before your function can handle its first request.",
    "This is a simulated response from the gateway's mock provider.",
    "Mock mode returns realistic latency and token counts without calling a paid API.",
)


def estimate_tokens(text: str) -> int:
    """~4 characters per token. Not exact but fine for fake numbers."""
    return max(1, round(len(text) / 4))


class MockProvider:
    def __init__(
        self,
        name: str,
        *,
        latency_ms: tuple[int, int] = (120, 450),
        failure: str | None = None,
        sleep=time.sleep,
    ) -> None:
        self.name = name
        self.latency_ms = latency_ms
        self.failure = failure
        self._sleep = sleep

    def complete(self, request: ChatRequest) -> ProviderResult:
        self._maybe_fail()
        self._sleep(random.randint(*self.latency_ms) / 1000)

        # same prompt -> same reply
        prompt = "\n".join(m.content for m in request.messages)
        digest = int(hashlib.sha256(prompt.encode()).hexdigest(), 16)
        content = f"[mock:{self.name}] {_REPLIES[digest % len(_REPLIES)]}"
        prompt_tokens = sum(estimate_tokens(m.content) + 4 for m in request.messages)
        return ProviderResult(
            provider=self.name,
            model=request.model,
            content=content,
            usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=estimate_tokens(content)),
        )

    def _maybe_fail(self) -> None:
        kind = self.failure
        if not kind:
            return
        if kind == "429":
            raise UpstreamRateLimitError(f"{self.name} rate limited the request (429).", upstream_status=429)
        if kind == "timeout":
            raise UpstreamTimeoutError(f"{self.name} did not respond in time.")
        if kind == "400":
            raise ProviderRequestError(f"{self.name} rejected the request (HTTP 400).", upstream_status=400)
        if kind.isdigit() and int(kind) >= 500:
            raise UpstreamError(f"{self.name} returned HTTP {kind}.", upstream_status=int(kind))
        raise ValueError(f"Unknown MOCK_FAILURES kind: {kind!r}")
