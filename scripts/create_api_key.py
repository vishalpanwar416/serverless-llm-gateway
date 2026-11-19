"""Create a new gateway API key, or check usage for one.

    python scripts/create_api_key.py --name "my laptop"
    python scripts/create_api_key.py --usage sk-gw-...

The key is only printed once, we just store its hash.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gateway.config import Settings  # noqa: E402
from gateway.store import DynamoApiKeyStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--name", help="label for a new key")
    group.add_argument("--usage", metavar="RAW_KEY", help="print usage counters for a key")
    args = parser.parse_args()

    settings = Settings.from_env()
    client = boto3.client(
        "dynamodb", region_name=settings.aws_region, endpoint_url=settings.dynamodb_endpoint
    )
    store = DynamoApiKeyStore(client, settings.api_keys_table)

    if args.usage:
        usage = store.get_usage(args.usage)
        usage.pop("key_hash", None)
        print(json.dumps(usage, indent=2, default=str))
        return

    raw_key = f"sk-gw-{secrets.token_urlsafe(24)}"
    key_id = f"key_{secrets.token_hex(4)}"
    store.create_api_key(raw_key, key_id=key_id, name=args.name)
    print(json.dumps({"key_id": key_id, "api_key": raw_key, "note": "save this, it won't be shown again"}, indent=2))


if __name__ == "__main__":
    main()
