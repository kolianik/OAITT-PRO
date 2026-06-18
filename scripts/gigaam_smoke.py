#!/usr/bin/env python3
"""End-to-end smoke test for the GigaAM path via the gateway async API.

Submits a job (``model=gigaam``), polls status, and validates the result shape
against API_transcriptions.md. The shape validator is pure and unit-tested
(tests/test_gigaam_smoke.py); the live runner needs a deployed stack.

Example:
    python scripts/gigaam_smoke.py --file sample.wav --token <API_KEY> \
        --base-url https://localhost:443 --diarize true --insecure
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

_TERMINAL = {"completed", "failed", "hallucination_filtered"}


def validate_transcription_shape(payload: Any, *, expect_diarization: bool) -> list[str]:
    """Return a list of contract problems (empty list == valid).

    Mirrors the GigaAM result contract documented in API_transcriptions.md:
    top-level ``text``/``duration``/``segments``; each segment has
    ``start``/``end``/``text`` and a ``speaker`` key (may be null); when
    diarization is requested at least one segment must carry a non-null speaker.
    """
    problems: list[str] = []
    if not isinstance(payload, dict):
        return ["result is not a JSON object"]
    if not isinstance(payload.get("text"), str):
        problems.append("missing/invalid 'text' (expected str)")
    if not isinstance(payload.get("duration"), (int, float)) or isinstance(payload.get("duration"), bool):
        problems.append("missing/invalid 'duration' (expected number)")
    segments = payload.get("segments")
    if not isinstance(segments, list):
        problems.append("missing/invalid 'segments' (expected list)")
        return problems

    speaker_seen = False
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            problems.append(f"segment[{i}] is not an object")
            continue
        for key, types in (("start", (int, float)), ("end", (int, float)), ("text", (str,))):
            value = seg.get(key)
            if not isinstance(value, types) or isinstance(value, bool):
                problems.append(f"segment[{i}] missing/invalid '{key}'")
        if "speaker" not in seg:
            problems.append(f"segment[{i}] missing 'speaker' (may be null)")
        elif seg.get("speaker"):
            speaker_seen = True
        for w in seg.get("words") or []:
            if not isinstance(w, dict) or not isinstance(w.get("word"), str):
                problems.append(f"segment[{i}] has a malformed word entry")
                break

    if expect_diarization and segments and not speaker_seen:
        problems.append("diarize=true but no segment has a non-null 'speaker'")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GigaAM e2e smoke test via the gateway async API.")
    parser.add_argument("--base-url", default=os.getenv("SMOKE_BASE_URL", "https://localhost:443"))
    parser.add_argument("--token", default=os.getenv("SMOKE_API_TOKEN", "default-client-key"))
    parser.add_argument("--file", required=True, help="audio file to submit")
    parser.add_argument("--model", default="gigaam")
    parser.add_argument("--diarize", default="true", choices=["true", "false"])
    parser.add_argument("--timeout", type=float, default=1800.0, help="seconds to wait for completion")
    parser.add_argument("--insecure", action="store_true", help="skip TLS verification (self-signed certs)")
    args = parser.parse_args(argv)

    import httpx

    expect_diar = args.diarize == "true"
    headers = {"Authorization": f"Bearer {args.token}"}
    with open(args.file, "rb") as fh:
        files = {"file": (os.path.basename(args.file), fh.read())}
    data = {"model": args.model, "diarize": args.diarize}

    with httpx.Client(timeout=60.0, verify=not args.insecure) as client:
        r = client.post(
            f"{args.base_url}/v1/audio/transcriptions/async",
            files=files,
            data=data,
            headers=headers,
        )
        if r.status_code != 202:
            print(f"submit failed: HTTP {r.status_code} {r.text[:300]}", file=sys.stderr)
            return 1
        job_id = r.json()["job_id"]
        print(f"submitted job {job_id} (model={args.model}, diarize={args.diarize}); polling...")

        deadline = time.time() + args.timeout
        status = "pending"
        result: dict = {}
        while time.time() < deadline:
            time.sleep(5)
            body = client.get(
                f"{args.base_url}/v1/audio/transcriptions/status/{job_id}",
                headers=headers,
            ).json()
            status = body.get("status", "unknown")
            print(f"  status={status}")
            if status in _TERMINAL:
                if status == "failed":
                    print(f"job failed: {body.get('error_message')}", file=sys.stderr)
                    return 1
                result = body.get("result") or {}
                break
        else:
            print("timed out waiting for job completion", file=sys.stderr)
            return 1

    problems = validate_transcription_shape(result, expect_diarization=expect_diar)
    print(json.dumps(
        {"status": status, "segments": len(result.get("segments", [])), "problems": problems},
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
