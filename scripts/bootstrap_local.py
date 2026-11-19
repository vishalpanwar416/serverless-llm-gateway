"""Creates the tables in LocalStack and adds the demo API key.

Fine to run more than once, it skips anything that already exists. Only for local
dev, in AWS the tables come from template.yaml.
"""

from __future__ import annotations

import os
import sys
import time

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gateway.config import Settings  # noqa: E402
from gateway.store import DynamoApiKeyStore, is_conditional_failure  # noqa: E402


def wait_for_dynamodb(client, attempts: int = 30) -> None:
    for _ in range(attempts):
        try:
            client.list_tables()
            return
        except (EndpointConnectionError, ClientError):
            time.sleep(1)
    raise SystemExit("DynamoDB endpoint did not become ready in time.")


def ensure_table(client, name: str, pk: str) -> None:
    try:
        client.create_table(
            TableName=name,
            AttributeDefinitions=[{"AttributeName": pk, "AttributeType": "S"}],
            KeySchema=[{"AttributeName": pk, "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        client.get_waiter("table_exists").wait(TableName=name)
        print(f"created table {name}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceInUseException":
            raise
        print(f"table {name} already exists")


def main() -> None:
    settings = Settings.from_env()
    client = boto3.client(
        "dynamodb", region_name=settings.aws_region, endpoint_url=settings.dynamodb_endpoint
    )
    wait_for_dynamodb(client)

    ensure_table(client, settings.api_keys_table, "key_hash")
    ensure_table(client, settings.cache_table, "cache_key")
    try:
        client.update_time_to_live(
            TableName=settings.cache_table,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"},
        )
    except ClientError as exc:
        if "already enabled" not in str(exc).lower():
            raise

    demo_key = os.environ.get("GATEWAY_DEMO_API_KEY")
    if demo_key:
        store = DynamoApiKeyStore(client, settings.api_keys_table)
        try:
            store.create_api_key(demo_key, key_id="key_local_demo", name="local demo key")
            print("seeded demo API key (from GATEWAY_DEMO_API_KEY)")
        except ClientError as exc:
            if not is_conditional_failure(exc):
                raise
            print("demo API key already present")


if __name__ == "__main__":
    main()
