#!/usr/bin/env python3
"""Docker HEALTHCHECK: poll /health and print human-readable status to stderr."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

HEALTH_URL = "http://127.0.0.1:9007/health"
TIMEOUT = 10


def main() -> int:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
            code = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        code = exc.code
    except Exception as exc:
        print(f"Health endpoint unreachable: {exc}", file=sys.stderr)
        return 1

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        print(f"Invalid health JSON (HTTP {code})", file=sys.stderr)
        return 1

    message = data.get("message") or data.get("status") or "unknown"
    if data.get("status") == "healthy" and data.get("ready") is True:
        return 0

    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
