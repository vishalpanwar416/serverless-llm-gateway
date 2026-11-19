"""Request/response models + validation. Plain dataclasses, no pydantic, to keep the package small."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .errors import InvalidRequestError

VALID_ROLES = frozenset({"system", "user", "assistant"})


@dataclass(frozen=True)
class Message:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: tuple[Message, ...]
    temperature: float | None = None
    max_tokens: int | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> "ChatRequest":
        if not isinstance(payload, dict):
            raise InvalidRequestError("Request body must be a JSON object.")

        model = payload.get("model")
        if not isinstance(model, str) or not model.strip():
            raise InvalidRequestError("'model' is required and must be a non-empty string.")

        raw_messages = payload.get("messages")
        if not isinstance(raw_messages, list) or not raw_messages:
            raise InvalidRequestError("'messages' is required and must be a non-empty array.")

        messages: list[Message] = []
        for i, m in enumerate(raw_messages):
            if not isinstance(m, dict):
                raise InvalidRequestError(f"messages[{i}] must be an object.")
            role, content = m.get("role"), m.get("content")
            if role not in VALID_ROLES:
                raise InvalidRequestError(
                    f"messages[{i}].role must be one of {sorted(VALID_ROLES)}."
                )
            if not isinstance(content, str):
                raise InvalidRequestError(f"messages[{i}].content must be a string.")
            messages.append(Message(role=role, content=content))

        temperature = payload.get("temperature")
        if temperature is not None:
            if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
                raise InvalidRequestError("'temperature' must be a number.")
            if not 0 <= temperature <= 2:
                raise InvalidRequestError("'temperature' must be between 0 and 2.")
            temperature = float(temperature)

        max_tokens = payload.get("max_tokens")
        if max_tokens is not None:
            if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
                raise InvalidRequestError("'max_tokens' must be a positive integer.")

        if payload.get("stream"):
            raise InvalidRequestError("'stream: true' is not supported by this gateway yet.")

        return cls(
            model=model.strip(),
            messages=tuple(messages),
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def with_model(self, model: str) -> "ChatRequest":
        return ChatRequest(model, self.messages, self.temperature, self.max_tokens)

    @property
    def is_deterministic(self) -> bool:
        return self.temperature == 0


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class ProviderResult:
    """Every provider adapter returns this, so the rest of the code doesn't care which provider it was."""

    provider: str
    model: str
    content: str
    usage: Usage


@dataclass
class ChatResponse:
    provider: str
    model: str
    content: str
    usage: Usage
    latency_ms: int
    cached: bool = False
    id: str = field(default_factory=lambda: new_request_id())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": "chat.completion",
            "created": int(time.time()),
            "provider": self.provider,
            "model": self.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": self.content},
                    "finish_reason": "stop",
                }
            ],
            "usage": self.usage.to_dict(),
            "latency_ms": self.latency_ms,
            "cached": self.cached,
        }


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:24]}"
