"""Base class for provider adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..errors import UpstreamError
from ..models import ChatRequest, ProviderResult, Usage


class Provider(ABC):
    """Each provider converts our ChatRequest into its own API format and back.

    complete() should return a ProviderResult and raise errors from errors.py
    (post_json already does that for HTTP errors). To add a new provider, subclass
    this and register it in registry.py.
    """

    name: str

    @abstractmethod
    def complete(self, request: ChatRequest) -> ProviderResult: ...

    # in case a provider returns 200 but the body is missing what we need
    @staticmethod
    def _require(data: Any, *path: str | int) -> Any:
        cur = data
        for key in path:
            try:
                cur = cur[key]
            except (KeyError, IndexError, TypeError):
                raise UpstreamError(
                    f"Upstream response missing field {'.'.join(map(str, path))!r}."
                ) from None
        return cur

    @staticmethod
    def _usage(prompt: Any, completion: Any) -> Usage:
        return Usage(prompt_tokens=int(prompt or 0), completion_tokens=int(completion or 0))
