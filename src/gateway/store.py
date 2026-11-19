"""DynamoDB stuff: API keys with their usage counters, and the response cache.

api keys table: key_hash is the partition key. Each item also has key_id, name, enabled,
created_at and the counters (total_prompt_tokens, total_completion_tokens,
request_count, cache_hits, last_used_at).

cache table: cache_key is the partition key, response is the JSON body, expires_at is the TTL.

We only store the sha256 of each key, never the key itself.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

from botocore.exceptions import ClientError


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


@dataclass(frozen=True)
class ApiKeyRecord:
    key_id: str
    name: str
    enabled: bool


class ApiKeyStore(Protocol):
    def get_api_key(self, key_hash: str) -> ApiKeyRecord | None: ...

    def record_usage(
        self, key_hash: str, *, prompt_tokens: int, completion_tokens: int, cache_hit: bool
    ) -> None: ...


class ResponseCache(Protocol):
    def get(self, cache_key: str) -> dict[str, Any] | None: ...

    def put(self, cache_key: str, response: dict[str, Any]) -> None: ...


class DynamoApiKeyStore:
    def __init__(self, client: Any, table_name: str) -> None:
        self.client = client
        self.table = table_name

    def get_api_key(self, key_hash: str) -> ApiKeyRecord | None:
        resp = self.client.get_item(
            TableName=self.table,
            Key={"key_hash": {"S": key_hash}},
            ProjectionExpression="key_id, #n, enabled",
            ExpressionAttributeNames={"#n": "name"},
        )
        item = resp.get("Item")
        if not item:
            return None
        return ApiKeyRecord(
            key_id=item.get("key_id", {}).get("S", "unknown"),
            name=item.get("name", {}).get("S", ""),
            enabled=item.get("enabled", {}).get("BOOL", True),
        )

    def record_usage(
        self, key_hash: str, *, prompt_tokens: int, completion_tokens: int, cache_hit: bool
    ) -> None:
        """Bumps the counters for a key.

        ADD runs inside DynamoDB, so two requests at the same time can't overwrite
        each other (unlike reading the value, adding, and writing it back).
        The condition is there so this can't create an item for a key that was deleted.
        """
        self.client.update_item(
            TableName=self.table,
            Key={"key_hash": {"S": key_hash}},
            UpdateExpression=(
                "ADD total_prompt_tokens :p, total_completion_tokens :c, "
                "request_count :one, cache_hits :hit SET last_used_at = :now"
            ),
            ConditionExpression="attribute_exists(key_hash)",
            ExpressionAttributeValues={
                ":p": {"N": str(prompt_tokens)},
                ":c": {"N": str(completion_tokens)},
                ":one": {"N": "1"},
                ":hit": {"N": "1" if cache_hit else "0"},
                ":now": {"S": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
            },
        )

    def create_api_key(self, raw_key: str, *, key_id: str, name: str) -> None:
        self.client.put_item(
            TableName=self.table,
            Item={
                "key_hash": {"S": hash_api_key(raw_key)},
                "key_id": {"S": key_id},
                "name": {"S": name},
                "enabled": {"BOOL": True},
                "created_at": {"S": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                "total_prompt_tokens": {"N": "0"},
                "total_completion_tokens": {"N": "0"},
                "request_count": {"N": "0"},
                "cache_hits": {"N": "0"},
            },
            ConditionExpression="attribute_not_exists(key_hash)",
        )

    def get_usage(self, raw_key: str) -> dict[str, Any]:
        item = self.client.get_item(
            TableName=self.table, Key={"key_hash": {"S": hash_api_key(raw_key)}}
        ).get("Item", {})
        return {k: (int(v["N"]) if "N" in v else next(iter(v.values()))) for k, v in item.items()}


class DynamoResponseCache:
    def __init__(self, client: Any, table_name: str, ttl_seconds: int) -> None:
        self.client = client
        self.table = table_name
        self.ttl = ttl_seconds

    def get(self, cache_key: str) -> dict[str, Any] | None:
        item = self.client.get_item(
            TableName=self.table, Key={"cache_key": {"S": cache_key}}
        ).get("Item")
        if not item:
            return None
        # dynamo doesn't delete expired items right away, so check it here too
        if int(item["expires_at"]["N"]) <= int(time.time()):
            return None
        return json.loads(item["response"]["S"])

    def put(self, cache_key: str, response: dict[str, Any]) -> None:
        self.client.put_item(
            TableName=self.table,
            Item={
                "cache_key": {"S": cache_key},
                "response": {"S": json.dumps(response)},
                "expires_at": {"N": str(int(time.time()) + self.ttl)},
            },
        )


def is_conditional_failure(exc: Exception) -> bool:
    return (
        isinstance(exc, ClientError)
        and exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"
    )
