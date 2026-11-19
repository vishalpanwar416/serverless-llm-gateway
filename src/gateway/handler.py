"""Lambda entry point. Handles both REST API (v1) and HTTP API (v2) events.

The boto3 client, providers and settings are created once when the module loads
(outside lambda_handler), so warm invocations don't rebuild them every time.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import boto3
from botocore.config import Config

from .auth import Authenticator
from .config import Settings
from .models import new_request_id
from .providers import build_registry
from .router import Router
from .service import Gateway, HttpResponse
from .store import DynamoApiKeyStore, DynamoResponseCache


def build_gateway(settings: Settings, dynamodb_client: Any | None = None) -> Gateway:
    client = dynamodb_client or boto3.client(
        "dynamodb",
        region_name=settings.aws_region,
        endpoint_url=settings.dynamodb_endpoint,  # set for localstack, empty in aws
        config=Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 3, "mode": "standard"}),
    )
    key_store = DynamoApiKeyStore(client, settings.api_keys_table)
    cache = (
        DynamoResponseCache(client, settings.cache_table, settings.cache_ttl_seconds)
        if settings.cache_enabled
        else None
    )
    return Gateway(
        authenticator=Authenticator(key_store),
        router=Router(build_registry(settings), settings),
        usage_store=key_store,
        cache=cache,
    )


SETTINGS = Settings.from_env()
GATEWAY = build_gateway(SETTINGS)


def parse_event(event: dict[str, Any]) -> tuple[str, str, dict[str, str], str | None, str]:
    """Pulls method, path, headers and body out of a v1 or v2 event."""
    ctx = event.get("requestContext") or {}
    if event.get("version") == "2.0":
        method = ctx.get("http", {}).get("method", "GET")
        path = event.get("rawPath", "/")
    else:
        method = event.get("httpMethod", "GET")
        path = event.get("path", "/")

    # remove the stage from the path (e.g. /prod/v1/... -> /v1/...)
    stage = ctx.get("stage")
    if stage and stage != "$default" and path.startswith(f"/{stage}/"):
        path = path[len(stage) + 1 :]

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items() if v is not None}
    body = event.get("body")
    if body and event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    request_id = ctx.get("requestId") or new_request_id()
    if not request_id.startswith("req_"):
        request_id = f"req_{request_id}"
    return method, path, headers, body, request_id


def to_proxy_response(resp: HttpResponse) -> dict[str, Any]:
    return {
        "statusCode": resp.status_code,
        "headers": {"content-type": "application/json", **resp.headers},
        "body": json.dumps(resp.body),
    }


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    method, path, headers, body, request_id = parse_event(event)
    resp = GATEWAY.handle(method=method, path=path, headers=headers, body=body, request_id=request_id)
    return to_proxy_response(resp)
