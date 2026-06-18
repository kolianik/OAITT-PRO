"""T-002: GIGAAM_DIARIZE_BATCH_SIZE caps Pyannote segmentation peak VRAM.

Pyannote's segmentation ``Inference`` runs at its built-in batch size (~32),
which drives diarization peak VRAM to ~9.7 GB on a 35-min file — an OOM on an
RTX 3060 6 GB. ``GIGAAM_DIARIZE_BATCH_SIZE`` (default 8) is threaded from config
into ``run_diarization`` and applied to ``pipeline._segmentation.batch_size``.

pyannote is **not** installed on this host, so these tests target the pure
helper plus the config/plumbing wiring — never the real pyannote pipeline.
"""
import importlib
import os
import sys
import types

import pytest

GIGAAM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam"))
sys.path.insert(0, GIGAAM_ROOT)

from pipeline import diarization as diar  # noqa: E402
from pipeline import orchestrator as orch  # noqa: E402


def _fake_pipeline_with_segmentation(batch_size: int = 32):
    seg = types.SimpleNamespace(batch_size=batch_size)
    return types.SimpleNamespace(_segmentation=seg)


# --- helper: applies the lever -----------------------------------------------

def test_apply_sets_segmentation_batch_size():
    p = _fake_pipeline_with_segmentation()
    diar._apply_segmentation_batch_size(p, 8)
    assert p._segmentation.batch_size == 8


@pytest.mark.parametrize("value", [None, 0])
def test_apply_noop_for_none_or_zero(value):
    p = _fake_pipeline_with_segmentation(batch_size=32)
    diar._apply_segmentation_batch_size(p, value)
    assert p._segmentation.batch_size == 32  # untouched


def test_apply_no_segmentation_attr_does_not_raise():
    """Guards against pyannote 4.0.4 community-1 naming the attribute differently."""
    p = types.SimpleNamespace()  # no _segmentation
    diar._apply_segmentation_batch_size(p, 8)  # must not raise


# --- plumbing: run_pipeline -> run_diarization -------------------------------

def test_run_pipeline_forwards_diarize_batch_size(monkeypatch, tmp_path):
    captured = {}

    def fake_run_diarization(original_wav_path, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(orch, "preprocess_audio", lambda *a, **k: None)
    monkeypatch.setattr(orch, "_audio_duration", lambda *a, **k: 1.0)
    monkeypatch.setattr(orch, "passthrough_clean", lambda *a, **k: None)
    monkeypatch.setattr(orch, "chunk_audio", lambda *a, **k: [])  # short-circuit
    monkeypatch.setattr(orch, "run_diarization", fake_run_diarization)

    orch.run_pipeline(
        str(tmp_path / "in.wav"),
        diarize=True,
        denoise_enabled=False,
        diarize_batch_size=8,
        hf_token="hf_xxx",
    )
    assert captured.get("diarize_batch_size") == 8


# --- config: main reads the env var ------------------------------------------

def _reload_main():
    import main as gigaam_main
    return importlib.reload(gigaam_main)


def test_main_default_diarize_batch_size(monkeypatch):
    monkeypatch.delenv("GIGAAM_DIARIZE_BATCH_SIZE", raising=False)
    m = _reload_main()
    assert m.GIGAAM_DIARIZE_BATCH_SIZE == 8


def test_main_clamps_diarize_batch_size_to_min_one(monkeypatch):
    monkeypatch.setenv("GIGAAM_DIARIZE_BATCH_SIZE", "0")
    m = _reload_main()
    assert m.GIGAAM_DIARIZE_BATCH_SIZE == 1
