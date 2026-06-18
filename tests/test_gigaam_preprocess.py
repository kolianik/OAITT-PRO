"""GigaAM ffmpeg preprocess diagnosability (agents.md §2.5 pipeline step 1).

A failing ffmpeg run must surface its stderr (clear RuntimeError), not an opaque
CalledProcessError with no context.
"""
from __future__ import annotations

import os
import sys

import pytest

GIGAAM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam"))
sys.path.insert(0, GIGAAM_ROOT)

import pipeline.preprocess as pp  # noqa: E402


class _FakeProc:
    def __init__(self, returncode: int, stderr: bytes):
        self.returncode = returncode
        self.stderr = stderr


def test_preprocess_raises_with_ffmpeg_stderr(monkeypatch, tmp_path):
    err = b"Invalid data found when processing input"
    monkeypatch.setattr(pp.subprocess, "run", lambda *a, **k: _FakeProc(1, err))
    out = str(tmp_path / "out.wav")
    with pytest.raises(RuntimeError) as ei:
        pp.preprocess_audio("bad.mp3", out)
    assert "Invalid data found" in str(ei.value)


def test_preprocess_returns_output_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr(pp.subprocess, "run", lambda *a, **k: _FakeProc(0, b""))
    out = str(tmp_path / "sub" / "out.wav")
    assert pp.preprocess_audio("in.wav", out) == out
