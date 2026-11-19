"""HTTP calls to the providers.

I'm using urllib3 because it already comes with botocore in the Lambda runtime, so no
extra dependency. The pool is created once at import, so warm Lambdas reuse connections.

This is also where provider status codes get turned into our error types.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

import urllib3

from .errors import (
    ProviderAuthError,
    ProviderRequestError,
    UpstreamError,
    UpstreamRateLimitError,
    UpstreamTimeoutError,
)

_POOL = urllib3.PoolManager(num_pools=8, maxsize=8, retries=False)


def classify_status(provider: str, status: int, body: str) -> Exception | None:
    """Returns the matching error for a provider status code, or None if it was a 2xx."""
    if 200 <= status < 300:
        return None
    # only pass on a short bit of the provider's message, not the whole body
    detail = _short_detail(body)
    s = {"upstream_status": status}
    if status == 429:
        return UpstreamRateLimitError(f"{provider} rate limited the request (429).", **s)
    if status >= 500:
        return UpstreamError(f"{provider} returned HTTP {status}.", **s)
    if status in (401, 403):
        return ProviderAuthError(f"{provider} rejected the gateway's credentials (HTTP {status}).", **s)
    if status == 408:
        return UpstreamTimeoutError(f"{provider} timed out (408).", **s)
    return ProviderRequestError(f"{provider} rejected the request (HTTP {status}): {detail}", **s)


def _short_detail(body: str) -> str:
    try:
        data = json.loads(body)
        err = data.get("error", data) if isinstance(data, dict) else data
        msg = err.get("message") if isinstance(err, dict) else err
        text = str(msg)
    except (ValueError, AttributeError):
        text = body
    return text.strip()[:200]


def post_json(
    provider: str,
    url: str,
    payload: Mapping[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float,
) -> dict[str, Any]:
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    try:
        resp = _POOL.request(
            "POST",
            url,
            body=json.dumps(payload).encode(),
            headers=all_headers,
            timeout=urllib3.Timeout(connect=min(3.0, timeout_seconds), read=timeout_seconds),
        )
    except (urllib3.exceptions.TimeoutError, urllib3.exceptions.ReadTimeoutError) as exc:
        raise UpstreamTimeoutError(
            f"{provider} did not respond within {timeout_seconds:g}s."
        ) from exc
    except urllib3.exceptions.HTTPError as exc:
        # connection refused, DNS, TLS etc. -> provider is unreachable, try the next one
        raise UpstreamError(f"{provider} is unreachable ({type(exc).__name__}).") from exc

    body = resp.data.decode("utf-8", errors="replace")
    error = classify_status(provider, resp.status, body)
    if error is not None:
        raise error
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise UpstreamError(f"{provider} returned a non-JSON response.") from exc
    if not isinstance(data, dict):
        raise UpstreamError(f"{provider} returned an unexpected response shape.")
    return data
