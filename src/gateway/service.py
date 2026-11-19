"""Main request flow: auth, validate, cache, route, then record usage.

Nothing Lambda specific in here. handler.py and scripts/local_server.py both just call
Gateway.handle().
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from .auth import Authenticator, Principal
from .cache import cache_key
from .errors import GatewayError, InvalidRequestError, MethodNotAllowedError, NotFoundError
from .jsonlog import log
from .models import ChatRequest, ChatResponse, Usage
from .router import RoutedResult, Router
from .store import ApiKeyStore, ResponseCache

CHAT_PATH = "/v1/chat/completions"


@dataclass
class HttpResponse:
    status_code: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


def error_body(exc: GatewayError, request_id: str) -> dict[str, Any]:
    """Same error format as OpenAI. No stack traces in here."""
    return {
        "error": {
            "message": exc.message,
            "type": exc.error_type,
            "code": exc.status_code,
            "request_id": request_id,
        }
    }


class Gateway:
    def __init__(
        self,
        *,
        authenticator: Authenticator,
        router: Router,
        usage_store: ApiKeyStore,
        cache: ResponseCache | None = None,
    ) -> None:
        self.authenticator = authenticator
        self.router = router
        self.usage_store = usage_store
        self.cache = cache

    def handle(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: str | None,
        request_id: str,
    ) -> HttpResponse:
        started = time.perf_counter()
        ctx: dict[str, Any] = {"request_id": request_id, "path": path}
        try:
            response = self._dispatch(method, path, headers, body, request_id, ctx)
        except GatewayError as exc:
            response = HttpResponse(exc.status_code, error_body(exc, request_id))
            ctx["error_type"] = exc.error_type
        except Exception as exc:  # noqa: BLE001 - catch-all so we never send back internal errors
            log("error", "unhandled_exception", request_id=request_id, error=type(exc).__name__)
            err = GatewayError("Internal server error.")
            response = HttpResponse(500, error_body(err, request_id))
            ctx["error_type"] = err.error_type

        latency_ms = int((time.perf_counter() - started) * 1000)
        response.headers.setdefault("x-request-id", request_id)
        log(
            "info" if response.status_code < 500 else "error",
            "request_completed",
            status_code=response.status_code,
            latency_ms=latency_ms,
            **ctx,
        )
        return response

    def _dispatch(self, method, path, headers, body, request_id, ctx) -> HttpResponse:
        path = path.rstrip("/") or "/"
        if path == "/health":
            return HttpResponse(200, {"status": "ok"})
        if path != CHAT_PATH:
            raise NotFoundError(f"No route for {path}.")
        if method.upper() != "POST":
            raise MethodNotAllowedError(f"{method} is not allowed on {CHAT_PATH}; use POST.")

        principal = self.authenticator.authenticate(headers)
        ctx["api_key_id"] = principal.masked_key

        try:
            payload = json.loads(body or "")
        except ValueError:
            raise InvalidRequestError("Request body must be valid JSON.") from None
        request = ChatRequest.from_payload(payload)
        ctx["requested_model"] = request.model
        ctx["target_provider"] = self.router.resolve(request.model)

        return self._chat(request, principal, request_id, ctx)

    def _chat(self, request: ChatRequest, principal: Principal, request_id: str, ctx) -> HttpResponse:
        started = time.perf_counter()
        key = cache_key(request) if (self.cache and request.is_deterministic) else None

        if key:
            cached = self._cache_get(key, request_id)
            if cached is not None:
                cached.update(
                    id=request_id,
                    cached=True,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
                ctx.update(cached=True, target_provider=cached.get("provider"))
                # no provider was called, so count the request but not tokens
                self._record_usage(principal, Usage(), cache_hit=True, request_id=request_id)
                return HttpResponse(200, cached, {"x-gateway-cache": "hit"})

        routed: RoutedResult = self.router.complete(request, request_id=request_id)
        result = routed.result
        response = ChatResponse(
            id=request_id,
            provider=result.provider,
            model=result.model,
            content=result.content,
            usage=result.usage,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        body = response.to_dict()
        headers = {"x-gateway-provider": result.provider}
        ctx.update(
            target_provider=result.provider,
            served_model=result.model,
            fallback_used=routed.fallback_used,
            attempts=len(routed.attempts),
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
        )
        if routed.fallback_used:
            headers["x-gateway-fallback"] = f"{routed.primary_provider}->{result.provider}"

        self._record_usage(principal, result.usage, cache_hit=False, request_id=request_id)
        if key:
            headers["x-gateway-cache"] = "miss"
            self._cache_put(key, body, request_id)
        return HttpResponse(200, body, headers)

    # If these dynamo writes fail we still return the answer. The LLM call already
    # happened (and was paid for), so failing the whole request seems worse.
    # We log an error instead so it can be alerted on.

    def _record_usage(self, principal: Principal, usage: Usage, *, cache_hit: bool, request_id: str) -> None:
        try:
            self.usage_store.record_usage(
                principal.key_hash,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cache_hit=cache_hit,
            )
        except Exception as exc:  # noqa: BLE001
            log("error", "usage_tracking_failed", request_id=request_id,
                api_key_id=principal.masked_key, error=type(exc).__name__)

    def _cache_get(self, key: str, request_id: str) -> dict[str, Any] | None:
        try:
            return self.cache.get(key)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            log("warning", "cache_read_failed", request_id=request_id, error=type(exc).__name__)
            return None

    def _cache_put(self, key: str, body: dict[str, Any], request_id: str) -> None:
        try:
            self.cache.put(key, body)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            log("warning", "cache_write_failed", request_id=request_id, error=type(exc).__name__)
