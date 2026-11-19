"""DynamoDB code, tested against moto."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from gateway.store import DynamoApiKeyStore, DynamoResponseCache, hash_api_key, is_conditional_failure

RAW = "sk-gw-store-test"


@pytest.fixture
def client():
    with mock_aws():
        c = boto3.client("dynamodb", region_name="ap-southeast-2")
        for name, pk in (("keys", "key_hash"), ("cache", "cache_key")):
            c.create_table(
                TableName=name,
                AttributeDefinitions=[{"AttributeName": pk, "AttributeType": "S"}],
                KeySchema=[{"AttributeName": pk, "KeyType": "HASH"}],
                BillingMode="PAY_PER_REQUEST",
            )
        yield c


def test_create_and_lookup_by_hash_only(client):
    store = DynamoApiKeyStore(client, "keys")
    store.create_api_key(RAW, key_id="key_1", name="n")
    item = client.scan(TableName="keys")["Items"][0]
    assert RAW not in str(item)  # only the hash should be saved
    assert store.get_api_key(hash_api_key(RAW)).key_id == "key_1"
    assert store.get_api_key(hash_api_key("other")) is None


def test_usage_counters_are_atomic_under_concurrency(client):
    store = DynamoApiKeyStore(client, "keys")
    store.create_api_key(RAW, key_id="key_1", name="n")
    h = hash_api_key(RAW)

    def hit(_):
        store.record_usage(h, prompt_tokens=10, completion_tokens=3, cache_hit=False)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(hit, range(40)))

    usage = store.get_usage(RAW)
    assert usage["request_count"] == 40
    assert usage["total_prompt_tokens"] == 400
    assert usage["total_completion_tokens"] == 120


def test_usage_update_does_not_create_unknown_keys(client):
    store = DynamoApiKeyStore(client, "keys")
    with pytest.raises(ClientError) as exc:
        store.record_usage(hash_api_key("ghost"), prompt_tokens=1, completion_tokens=1, cache_hit=False)
    assert is_conditional_failure(exc.value)


def test_cache_round_trip_and_expiry(client, monkeypatch):
    cache = DynamoResponseCache(client, "cache", ttl_seconds=60)
    cache.put("k", {"provider": "openai"})
    assert cache.get("k") == {"provider": "openai"}
    assert cache.get("missing") is None

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 120)
    assert cache.get("k") is None  # item is still in the table but past its expiry
