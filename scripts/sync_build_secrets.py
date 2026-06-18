#!/usr/bin/env python3
"""Write secrets/hf_token from HF_TOKEN env or .env for docker compose build."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
OUT = ROOT / "secrets" / "hf_token"


def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, _, value = line.partition("=")
    return key.strip(), value.strip().strip('"').strip("'")


def _token_from_env_file() -> str:
    if not ENV_FILE.is_file():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        parsed = _parse_line(line)
        if parsed and parsed[0] == "HF_TOKEN":
            return parsed[1]
    return ""


def main() -> None:
    token = os.environ.get("HF_TOKEN", "").strip() or _token_from_env_file()
    if not token:
        print(
            "ERROR: HF_TOKEN not found in environment or .env. "
            "Set HF_TOKEN in .env, then run: python scripts/sync_build_secrets.py",
            file=sys.stderr,
        )
        sys.exit(1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(token + "\n", encoding="utf-8")
    print(f"Synced HF_TOKEN to {OUT.relative_to(ROOT)} (length={len(token)})")


if __name__ == "__main__":
    main()
