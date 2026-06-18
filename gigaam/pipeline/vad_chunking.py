"""Step 2: Silero VAD chunking with Force Split fallback."""
from __future__ import annotations

import logging
import os
from typing import List, Tuple

import numpy as np
import soundfile as sf
import torch

from .preprocess import SAMPLE_RATE
from .types import AudioChunk

logger = logging.getLogger("gigaam-service.vad")

MAX_CHUNK_SEC = 24.9
FORCE_SPLIT_WINDOW_START = 20.0
FORCE_SPLIT_WINDOW_END = 24.9
RMS_WINDOW_SEC = 0.15

_vad_model = None
_vad_utils = None


def _load_silero():
    global _vad_model, _vad_utils
    if _vad_model is None:
        from silero_vad import get_speech_timestamps, load_silero_vad

        _vad_model = load_silero_vad()
        _vad_utils = get_speech_timestamps
    return _vad_model, _vad_utils


def _read_mono(path: str) -> np.ndarray:
    audio, sr = sf.read(path, dtype="float32")
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    if sr != SAMPLE_RATE:
        import torchaudio

        t = torch.from_numpy(audio).float().unsqueeze(0)
        t = torchaudio.functional.resample(t, sr, SAMPLE_RATE)
        audio = t.squeeze(0).numpy()
    return audio


def _speech_segments(
    audio: np.ndarray,
    *,
    min_silence_ms: int = 300,
) -> List[Tuple[float, float]]:
    model, get_ts = _load_silero()
    wav_t = torch.from_numpy(audio).float()
    stamps = get_ts(
        wav_t,
        model,
        sampling_rate=SAMPLE_RATE,
        min_silence_duration_ms=min_silence_ms,
    )
    if not stamps:
        duration = len(audio) / SAMPLE_RATE
        return [(0.0, duration)]

    segments: List[Tuple[float, float]] = []
    for s in stamps:
        start = s["start"] / SAMPLE_RATE
        end = s["end"] / SAMPLE_RATE
        if end - start > 0.05:
            segments.append((start, end))
    return segments or [(0.0, len(audio) / SAMPLE_RATE)]


def _force_split_point(audio: np.ndarray, start: float, end: float) -> float:
    """Find split time at minimum RMS in [start+min(20s, dur/2), start+24.9s] window."""
    duration = end - start
    rel_start = min(FORCE_SPLIT_WINDOW_START, duration * 0.5)
    rel_end = min(FORCE_SPLIT_WINDOW_END, duration - 0.1)
    if rel_end <= rel_start:
        return start + duration / 2.0

    win = int(RMS_WINDOW_SEC * SAMPLE_RATE)
    search_s = int((start + rel_start) * SAMPLE_RATE)
    search_e = int((start + rel_end) * SAMPLE_RATE)
    region = audio[search_s:search_e]
    if len(region) <= win:
        return start + duration / 2.0

    rms = np.array(
        [
            np.sqrt(np.mean(region[i : i + win] ** 2) + 1e-12)
            for i in range(0, len(region) - win, win // 2 or 1)
        ]
    )
    min_i = int(np.argmin(rms))
    split_sample = search_s + min_i * (win // 2 or 1) + win // 2
    return split_sample / SAMPLE_RATE


def _split_long_segment(
    audio: np.ndarray, start: float, end: float
) -> List[Tuple[float, float]]:
    """Recursively split segments longer than MAX_CHUNK_SEC."""
    duration = end - start
    if duration <= MAX_CHUNK_SEC:
        return [(start, end)]

    # Try finer Silero inside segment (optional if silero installed)
    try:
        s_idx = int(start * SAMPLE_RATE)
        e_idx = int(end * SAMPLE_RATE)
        sub = audio[s_idx:e_idx]
        finer = _speech_segments(sub, min_silence_ms=100)
        if len(finer) > 1:
            out: List[Tuple[float, float]] = []
            for fs, fe in finer:
                gs, ge = start + fs, start + fe
                out.extend(_split_long_segment(audio, gs, ge))
            if all(ge - gs <= MAX_CHUNK_SEC for gs, ge in out):
                return out
    except (ImportError, ModuleNotFoundError):
        pass

    mid = _force_split_point(audio, start, end)
    left = _split_long_segment(audio, start, mid)
    right = _split_long_segment(audio, mid, end)
    return left + right


def chunk_audio(clean_wav_path: str, chunks_dir: str) -> List[AudioChunk]:
    """VAD + chunking; writes per-chunk WAV files."""
    os.makedirs(chunks_dir, exist_ok=True)
    audio = _read_mono(clean_wav_path)

    raw_segments = _speech_segments(audio)
    final_segments: List[Tuple[float, float]] = []
    for start, end in raw_segments:
        final_segments.extend(_split_long_segment(audio, start, end))

    chunks: List[AudioChunk] = []
    for idx, (start, end) in enumerate(final_segments):
        assert end - start <= MAX_CHUNK_SEC + 0.01, f"chunk {idx} exceeds limit"
        s_idx = int(start * SAMPLE_RATE)
        e_idx = int(end * SAMPLE_RATE)
        chunk_path = os.path.join(chunks_dir, f"chunk_{idx:05d}.wav")
        sf.write(chunk_path, audio[s_idx:e_idx], SAMPLE_RATE, subtype="PCM_16")
        chunks.append(
            AudioChunk(
                chunk_id=idx,
                start_time=start,
                end_time=end,
                file_path=chunk_path,
            )
        )

    logger.info("VAD produced %d chunks (max %.1fs)", len(chunks), MAX_CHUNK_SEC)
    return chunks
