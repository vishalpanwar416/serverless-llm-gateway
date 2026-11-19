from __future__ import annotations

import pytest

from gateway.auth import Authenticator, extract_bearer_token
from gateway.errors import AuthenticationError
from gateway.jsonlog import mask_key
from gateway.store import ApiKeyRecord, hash_api_key

from .conftest import VALID_KEY, FakeKeyStore


def test_valid_key_authenticates():
    principal = Authenticator(FakeKeyStore()).authenticate({"Authorization": f"Bearer {VALID_KEY}"})
    assert principal.record.key_id == "key_test"
    assert principal.key_hash == hash_api_key(VALID_KEY)
    assert VALID_KEY not in principal.masked_key


def test_header_name_is_case_insensitive():
    assert extract_bearer_token({"authorization": "bearer abc"}) == "abc"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": ""}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}, {"Authorization": "abc"}],
)
def test_missing_or_malformed_header_rejected(headers):
    with pytest.raises(AuthenticationError) as exc:
        Authenticator(FakeKeyStore()).authenticate(headers)
    assert exc.value.status_code == 401


def test_unknown_key_rejected():
    with pytest.raises(AuthenticationError):
        Authenticator(FakeKeyStore()).authenticate({"Authorization": "Bearer sk-gw-wrong"})


def test_disabled_key_rejected():
    store = FakeKeyStore({hash_api_key(VALID_KEY): ApiKeyRecord("key_test", "t", enabled=False)})
    with pytest.raises(AuthenticationError):
        Authenticator(store).authenticate({"Authorization": f"Bearer {VALID_KEY}"})


def test_mask_key():
    assert mask_key("sk-gw-local-demo-key") == "sk-g****-key"
    assert mask_key("short-key") == "*********"
    assert mask_key(None) == "-"
