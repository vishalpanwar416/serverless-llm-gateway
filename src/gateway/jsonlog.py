"""JSON logs to stdout so they're easy to query in CloudWatch.

Don't log prompts, responses or full API keys here.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any


def mask_key(raw_key: str | None) -> str:
    """sk-gw-local-demo-123456 -> sk-g****3456 (enough to tell keys apart in logs)."""
    if not raw_key:
        return "-"
    if len(raw_key) <= 12:
        return "*" * len(raw_key)
    return f"{raw_key[:4]}****{raw_key[-4:]}"


def log(level: str, message: str, **fields: Any) -> None:
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
        "level": level.upper(),
        "message": message,
        **{k: v for k, v in fields.items() if v is not None},
    }
    sys.stdout.write(json.dumps(record, default=str) + "\n")
    sys.stdout.flush()
