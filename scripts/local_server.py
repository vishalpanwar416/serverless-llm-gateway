"""Small local server that pretends to be API Gateway.

Turns each request into an HTTP API (v2) event and calls the same lambda_handler
that runs in AWS.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gateway.handler import lambda_handler  # noqa: E402


class ApiGatewayShim(BaseHTTPRequestHandler):
    server_version = "llm-gateway-local"

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else None
        path, _, query = self.path.partition("?")
        event = {
            "version": "2.0",
            "rawPath": path,
            "rawQueryString": query,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
            "isBase64Encoded": False,
            "requestContext": {
                "http": {"method": self.command, "path": path},
                "requestId": uuid.uuid4().hex[:24],
                "stage": "$default",
            },
        }
        result = lambda_handler(event, None)
        payload = result["body"].encode()
        self.send_response(result["statusCode"])
        for k, v in result.get("headers", {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _handle

    def log_message(self, *args) -> None:  # the gateway logs requests itself
        pass


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), ApiGatewayShim)
    print(json.dumps({"level": "INFO", "message": "local_server_started", "port": port}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
