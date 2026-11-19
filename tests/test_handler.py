from __future__ import annotations

import base64
import json

from gateway import handler
from gateway.service import HttpResponse


def test_parse_http_api_v2_event():
    event = {
        "version": "2.0",
        "rawPath": "/v1/chat/completions",
        "headers": {"Authorization": "Bearer x"},
        "body": base64.b64encode(b'{"a":1}').decode(),
        "isBase64Encoded": True,
        "requestContext": {"http": {"method": "POST"}, "requestId": "abc", "stage": "$default"},
    }
    method, path, headers, body, rid = handler.parse_event(event)
    assert (method, path, body, rid) == ("POST", "/v1/chat/completions", '{"a":1}', "req_abc")
    assert headers == {"authorization": "Bearer x"}


def test_parse_rest_v1_event_strips_stage():
    event = {
        "httpMethod": "POST",
        "path": "/prod/v1/chat/completions",
        "headers": {"Content-Type": "application/json"},
        "body": "{}",
        "requestContext": {"requestId": "r1", "stage": "prod"},
    }
    method, path, *_ = handler.parse_event(event)
    assert (method, path) == ("POST", "/v1/chat/completions")


def test_proxy_response_shape():
    out = handler.to_proxy_response(HttpResponse(200, {"ok": True}, {"x-request-id": "req_1"}))
    assert out["statusCode"] == 200
    assert out["headers"]["content-type"] == "application/json"
    assert json.loads(out["body"]) == {"ok": True}


def test_clients_are_built_once_at_import_time():
    # the gateway and boto3 client are created once at module level, not per request
    assert handler.GATEWAY is handler.GATEWAY
    assert handler.GATEWAY.usage_store.client is handler.GATEWAY.authenticator.store.client
