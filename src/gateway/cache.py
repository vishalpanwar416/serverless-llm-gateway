"""Cache key for temperature=0 requests (exact match only)."""

from __future__ import annotations

import hashlib
import json

from .models import ChatRequest


def cache_key(request: ChatRequest) -> str:
    """sha256 of model + messages + max_tokens (max_tokens changes the output so it's included).

    Uses the model the client asked for, so if the answer came from a fallback, the
    next identical request still hits the cache instead of trying the primary again.
    """
    canonical = json.dumps(
        {
            "model": request.model,
            "messages": [m.to_dict() for m in request.messages],
            "max_tokens": request.max_tokens,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
