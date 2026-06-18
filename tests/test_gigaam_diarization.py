"""GigaAM diarization availability contract (agents.md §2.5 "Diarization").

Diarization requires HF_TOKEN at runtime and a prefetched Pyannote model. When a
deployment cannot diarize, ``diarize=true`` must be rejected *early* with HTTP 400
(documented), not fail deep in the pipeline as an opaque 500. Plain transcription
must keep working.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

GIGAAM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam"))
sys.path.insert(0, GIGAAM_ROOT)

from fastapi.testclient import TestClient  # noqa: E402

import main as gigaam_main  # noqa: E402
from bootstrap_state import STATE as LIVE_STATE  # noqa: E402
from bootstrap_models import (  # noqa: E402
    BootstrapConfig,
    PYANNOTE_MODEL,
    diarization_available,
)

_FAKE_RESULT = {"text": "ok", "duration": 1.0, "language": "ru", "segments": []}
_UPLOAD = "/v1/audio/transcriptions"


def _cfg(tmp: str, hf_token: str) -> BootstrapConfig:
    return BootstrapConfig(
        cache_dir=tmp,
        weights_dir=os.path.join(tmp, "gigaam"),
        onnx_dir=os.path.join(tmp, "gigaam_onnx"),
        deepfilter_dir=os.path.join(tmp, "deepfilter"),
        model="v3_e2e_rnnt",
        align_model="jonatasgrosman/wav2vec2-xls-r-1b-russian",
        denoise_enabled=True,
        prefetch_diarization=True,
        offline_mode=False,
        prune_pytorch=False,
        hf_token=hf_token,
    )


def _seed_pyannote_cache(cache_dir: str) -> None:
    slug = "models--" + PYANNOTE_MODEL.replace("/", "--")
    snap = os.path.join(cache_dir, "hub", slug, "snapshots", "abc")
    os.makedirs(snap, exist_ok=True)
    Path(snap, "config.yaml").write_text("x", encoding="utf-8")


@pytest.fixture
def ready_app(monkeypatch):
    monkeypatch.setattr(gigaam_main, "ensure_models", lambda *a, **k: None)
    monkeypatch.setattr(
        gigaam_main, "process_transcription_from_file", lambda *a, **k: dict(_FAKE_RESULT)
    )
    prev = LIVE_STATE.snapshot()
    LIVE_STATE.update(status="healthy", ready=True, message="ready-for-test")
    try:
        yield
    finally:
        LIVE_STATE.update(**prev)


def _post(client, diarize: str):
    return client.post(
        _UPLOAD,
        files={"file": ("test.wav", b"RIFFfakeaudio")},
        data={"diarize": diarize},
    )


# --- pure helper -------------------------------------------------------------

def test_diarization_available_false_without_token(tmp_path):
    _seed_pyannote_cache(str(tmp_path))
    assert diarization_available(_cfg(str(tmp_path), hf_token="")) is False


def test_diarization_available_false_without_cache(tmp_path):
    assert diarization_available(_cfg(str(tmp_path), hf_token="hf_xxx")) is False


def test_diarization_available_true_with_token_and_cache(tmp_path):
    _seed_pyannote_cache(str(tmp_path))
    assert diarization_available(_cfg(str(tmp_path), hf_token="hf_xxx")) is True


# --- request guard -----------------------------------------------------------

def test_diarize_without_token_returns_400(ready_app, monkeypatch):
    monkeypatch.setattr(gigaam_main, "HF_TOKEN", "")
    LIVE_STATE.update(pyannote_cached=False)
    with TestClient(gigaam_main.app) as client:
        resp = _post(client, "true")
    assert resp.status_code == 400
    assert "diariz" in resp.json()["detail"].lower()


def test_diarize_without_pyannote_cache_returns_400(ready_app, monkeypatch):
    monkeypatch.setattr(gigaam_main, "HF_TOKEN", "hf_xxx")
    LIVE_STATE.update(pyannote_cached=False)
    with TestClient(gigaam_main.app) as client:
        resp = _post(client, "true")
    assert resp.status_code == 400


def test_diarize_with_token_and_cache_allowed(ready_app, monkeypatch):
    monkeypatch.setattr(gigaam_main, "HF_TOKEN", "hf_xxx")
    LIVE_STATE.update(pyannote_cached=True)
    with TestClient(gigaam_main.app) as client:
        resp = _post(client, "true")
    assert resp.status_code == 200


def test_transcription_without_diarize_always_allowed(ready_app, monkeypatch):
    monkeypatch.setattr(gigaam_main, "HF_TOKEN", "")
    LIVE_STATE.update(pyannote_cached=False)
    with TestClient(gigaam_main.app) as client:
        resp = _post(client, "false")
    assert resp.status_code == 200
