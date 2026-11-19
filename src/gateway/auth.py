"""Checks the Bearer key against the keys table."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Mapping

from .errors import AuthenticationError
from .jsonlog import mask_key
from .store import ApiKeyRecord, ApiKeyStore, hash_api_key


@dataclass(frozen=True)
class Principal:
    key_hash: str
    masked_key: str
    record: ApiKeyRecord


def extract_bearer_token(headers: Mapping[str, str]) -> str:
    # header names can come in any case from api gateway
    value = next((v for k, v in headers.items() if k.lower() == "authorization"), None)
    if not value:
        raise AuthenticationError("Missing Authorization header. Use 'Authorization: Bearer <key>'.")
    scheme, _, token = value.strip().partition(" ")
    if not hmac.compare_digest(scheme.lower(), "bearer") or not token.strip():
        raise AuthenticationError("Authorization header must use the Bearer scheme.")
    return token.strip()


class Authenticator:
    def __init__(self, store: ApiKeyStore) -> None:
        self.store = store

    def authenticate(self, headers: Mapping[str, str]) -> Principal:
        raw_key = extract_bearer_token(headers)
        # look it up by hash, the raw key never goes to dynamo or the logs
        key_hash = hash_api_key(raw_key)
        record = self.store.get_api_key(key_hash)
        if record is None or not record.enabled:
            # same message either way so nobody can tell if a key exists
            raise AuthenticationError("Invalid API key.")
        return Principal(key_hash=key_hash, masked_key=mask_key(raw_key), record=record)
