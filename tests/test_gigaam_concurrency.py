"""GigaAM web-layer concurrency contract (agents.md §2.5 "Health & concurrency").

These tests pin two documented properties of the FastAPI service:

1. A transcription runs *off the event loop*, so ``GET /health`` stays responsive
   while a job is in flight (the Docker healthcheck must not flap during long files).
2. Pipeline execution is *single-flight*: only one transcription runs at a time,
   protecting the shared per-stage model singletons.

The heavy pipeline is replaced with a controllable fake, so no model/GPU is needed.
"""
from __future__ import annotations

import os
import sys
import threading
import time

import pytest

GIGAAM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam"))
sys.path.insert(0, GIGAAM_ROOT)

from fastapi.testclient import TestClient  # noqa: E402

import main as gigaam_main  # noqa: E402
from bootstrap_state import STATE as LIVE_STATE  # noqa: E402

_FAKE_RESULT = {"text": "ok", "duration": 1.0, "language": "ru", "segments": []}
_UPLOAD = "/v1/audio/transcriptions"


@pytest.fixture
def ready_app(monkeypatch):
    """App with models marked ready and bootstrap disabled (no real downloads)."""
    monkeypatch.setattr(gigaam_main, "ensure_models", lambda *a, **k: None)
    prev = LIVE_STATE.snapshot()
    LIVE_STATE.update(status="healthy", ready=True, message="ready-for-test")
    try:
        yield
    finally:
        LIVE_STATE.update(**prev)


def _post_audio(client: TestClient) -> int:
    resp = client.post(
        _UPLOAD,
        files={"file": ("test.wav", b"RIFFfakeaudio")},
        data={"diarize": "false"},
    )
    return resp.status_code


def test_health_responsive_during_transcription(ready_app, monkeypatch):
    """While a transcription is running, GET /health must answer promptly."""
    started = threading.Event()
    release = threading.Event()

    def fake_pipeline(*args, **kwargs):
        started.set()
        # Emulate a long-running CPU/GPU job that holds the worker.
        release.wait(timeout=30)
        return dict(_FAKE_RESULT)

    monkeypatch.setattr(gigaam_main, "process_transcription_from_file", fake_pipeline)

    transcribe_result: dict = {}
    health_result: dict = {}
    health_done = threading.Event()

    # Context-managed TestClient => a single shared event loop for all requests,
    # which is what makes event-loop blocking observable.
    with TestClient(gigaam_main.app) as client:

        def do_transcribe():
            try:
                transcribe_result["status"] = _post_audio(client)
            except Exception as exc:  # pragma: no cover - defensive
                transcribe_result["error"] = repr(exc)

        def do_health():
            try:
                health_result["status"] = client.get("/health").status_code
            except Exception as exc:  # pragma: no cover - defensive
                health_result["error"] = repr(exc)
            finally:
                health_done.set()

        job = threading.Thread(target=do_transcribe)
        job.start()
        assert started.wait(timeout=10), "transcription handler never started"

        probe = threading.Thread(target=do_health)
        probe.start()

        # Core assertion: health returns even though a job occupies the worker.
        responsive = health_done.wait(timeout=5)

        release.set()
        job.join(timeout=15)
        probe.join(timeout=15)

    assert responsive, "/health blocked while a transcription was running (event loop blocked)"
    assert health_result.get("status") == 200, health_result
    assert transcribe_result.get("status") == 200, transcribe_result


def test_pipeline_runs_single_flight(ready_app, monkeypatch):
    """A second transcription must not enter the pipeline while the first holds it."""
    guard = threading.Lock()
    entered: list[int] = []
    counter = {"n": 0}
    first_in = threading.Event()
    release_first = threading.Event()

    def fake_pipeline(*args, **kwargs):
        with guard:
            counter["n"] += 1
            cid = counter["n"]
            entered.append(cid)
        if cid == 1:
            first_in.set()
            release_first.wait(timeout=30)
        return dict(_FAKE_RESULT)

    monkeypatch.setattr(gigaam_main, "process_transcription_from_file", fake_pipeline)

    with TestClient(gigaam_main.app) as client:
        t1 = threading.Thread(target=lambda: _post_audio(client))
        t1.start()
        assert first_in.wait(timeout=10), "first job never started"

        t2 = threading.Thread(target=lambda: _post_audio(client))
        t2.start()
        # Give the second request ample time to reach the pipeline if it were allowed.
        time.sleep(0.5)
        with guard:
            entered_during_hold = list(entered)

        release_first.set()
        t1.join(timeout=15)
        t2.join(timeout=15)

    assert entered_during_hold == [1], (
        f"second job entered the pipeline while the first held single-flight: {entered_during_hold}"
    )
