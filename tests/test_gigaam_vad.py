"""Tests for VAD chunking and Force Split."""
import os
import sys
import tempfile

import numpy as np
import soundfile as sf
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam")))

from pipeline.vad_chunking import MAX_CHUNK_SEC, _force_split_point, _split_long_segment, chunk_audio


def test_force_split_point_in_window():
    sr = 16000
    duration = 30.0
    rng = np.random.default_rng(0)
    audio = rng.standard_normal(int(duration * sr)).astype(np.float32) * 0.1
    # Carve a clear low-energy region near 22s so the min-RMS split is deterministic.
    q = int(22.0 * sr)
    audio[q : q + int(0.3 * sr)] = 0.0
    mid = _force_split_point(audio, 0.0, duration)
    assert 21.0 <= mid <= 23.0


def test_force_split_produces_sub_25s_segments():
    sr = 16000
    duration = 30.0
    audio = np.random.randn(int(duration * sr)).astype(np.float32) * 0.01
    segments = _split_long_segment(audio, 0.0, duration)
    assert len(segments) >= 2
    for start, end in segments:
        assert end - start <= MAX_CHUNK_SEC + 0.01


def test_chunk_audio_max_duration(tmp_path):
    sr = 16000
    duration = 35.0
    t = np.linspace(0, duration, int(duration * sr), endpoint=False)
    audio = (0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    wav_path = tmp_path / "long.wav"
    sf.write(str(wav_path), audio, sr)

    with tempfile.TemporaryDirectory() as chunks_dir:
        pytest.importorskip("silero_vad")
        chunks = chunk_audio(str(wav_path), chunks_dir)
        for c in chunks:
            assert c.end_time - c.start_time <= MAX_CHUNK_SEC + 0.01
