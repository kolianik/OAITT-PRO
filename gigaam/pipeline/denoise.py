"""Step 1b: DeepFilterNet3 denoise on GPU (48 kHz) with resample back to 16 kHz.

Audio is processed in overlapping windows (default 30 s, 2 s overlap) to keep peak
VRAM bounded to ~1 GB regardless of file duration.  Windows are stitched with a linear
crossfade over the overlap region.  On per-window OOM the window is halved and retried;
if the minimum window still OOMs, that window falls back to CPU.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional, Tuple

import numpy as np
import soundfile as sf
import torch
import torchaudio

from .memory import flush_memory
from .preprocess import SAMPLE_RATE

logger = logging.getLogger("gigaam-service.denoise")

DENOISE_SR = 48000
DENOISE_CHUNK_SEC = float(os.environ.get("GIGAAM_DENOISE_CHUNK_SEC", "30"))
DENOISE_OVERLAP_SEC = float(os.environ.get("GIGAAM_DENOISE_OVERLAP_SEC", "2"))
DENOISE_MIN_CHUNK_SEC = 1.0

_DEEPFILTER_DIR = os.environ.get("GIGAAM_DEEPFILTER_DIR", "/app/data/deepfilter")


def _deepfilter_model_dir(model_name: str) -> str:
    """Resolve the on-volume model directory, matching deepfilter_model_dir() in bootstrap."""
    return os.path.join(_DEEPFILTER_DIR, "DeepFilterNet", model_name)


def _resample(wav: torch.Tensor, orig_sr: int, target_sr: int) -> torch.Tensor:
    if orig_sr == target_sr:
        return wav
    return torchaudio.functional.resample(wav, orig_freq=orig_sr, new_freq=target_sr)


def _is_oom(exc: Exception) -> bool:
    """Return True if exc is a CUDA out-of-memory error."""
    if isinstance(exc, torch.cuda.OutOfMemoryError):
        return True
    msg = str(exc).lower()
    return "out of memory" in msg or "cuda out of memory" in msg


def _enhance_one_adaptive(
    enhance_fn: Any,
    model: Any,
    df_state: Any,
    wav_slice: torch.Tensor,
    df_enhance_mod: Any,
    device_str: str,
    torch_device: torch.device,
    min_chunk_samples: int,
) -> torch.Tensor:
    """Enhance a single slice with adaptive OOM fallback.

    On OOM the slice is halved recursively; if it still OOMs at min size, runs on CPU.
    """
    try:
        return enhance_fn(model, df_state, wav_slice)
    except Exception as exc:
        if not _is_oom(exc):
            raise
        # OOM path: try to free cache and recurse with smaller slices
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        n = wav_slice.shape[-1]
        if n // 2 >= min_chunk_samples:
            logger.warning(
                "Denoise OOM on %d samples (%.1fs @ 48kHz); halving to %d",
                n, n / DENOISE_SR, n // 2,
            )
            mid = n // 2
            left_slice = wav_slice[..., :mid]
            right_slice = wav_slice[..., mid:]
            left_out = _enhance_one_adaptive(
                enhance_fn, model, df_state, left_slice,
                df_enhance_mod, device_str, torch_device, min_chunk_samples,
            )
            right_out = _enhance_one_adaptive(
                enhance_fn, model, df_state, right_slice,
                df_enhance_mod, device_str, torch_device, min_chunk_samples,
            )
            return torch.cat([left_out, right_out], dim=-1)

        # CPU fallback for this slice
        logger.warning(
            "Denoise OOM on minimum slice (%d samples); falling back to CPU for this window",
            n,
        )
        orig_get_device = df_enhance_mod.get_device
        model_cpu = model.cpu()
        df_enhance_mod.get_device = lambda: torch.device("cpu")
        try:
            result = enhance_fn(model_cpu, df_state, wav_slice.cpu())
        finally:
            df_enhance_mod.get_device = orig_get_device
            if device_str != "cpu":
                model.to(torch_device)
        return result


def _enhance_windowed(
    enhance_fn: Any,
    model: Any,
    df_state: Any,
    wav_48k: torch.Tensor,
    chunk_samples: int,
    overlap_samples: int,
    df_enhance_mod: Any,
    device_str: str,
    torch_device: torch.device,
    min_chunk_samples: int,
) -> torch.Tensor:
    """Process wav_48k in overlapping windows; stitch with linear crossfade."""
    n = wav_48k.shape[-1]
    if n <= chunk_samples:
        return _enhance_one_adaptive(
            enhance_fn, model, df_state, wav_48k.unsqueeze(0),
            df_enhance_mod, device_str, torch_device, min_chunk_samples,
        ).squeeze(0)

    hop = chunk_samples - overlap_samples
    out = torch.zeros(n, dtype=wav_48k.dtype)

    # Linear fade-in / fade-out ramp for crossfade stitching
    ramp_up = torch.linspace(0.0, 1.0, overlap_samples) if overlap_samples > 0 else torch.empty(0)
    ramp_dn = 1.0 - ramp_up

    pos = 0
    while pos < n:
        end = min(pos + chunk_samples, n)
        wav_slice = wav_48k[pos:end]

        enhanced_slice = _enhance_one_adaptive(
            enhance_fn, model, df_state, wav_slice.unsqueeze(0),
            df_enhance_mod, device_str, torch_device, min_chunk_samples,
        ).squeeze(0).cpu()

        slice_len = enhanced_slice.shape[-1]

        if pos == 0 or overlap_samples == 0:
            out[pos:pos + slice_len] = enhanced_slice
        else:
            # Crossfade the leading overlap with the tail of the previous window
            xfade_len = min(overlap_samples, slice_len, n - pos)
            if xfade_len > 0:
                r_up = ramp_up[:xfade_len]
                r_dn = ramp_dn[:xfade_len]
                out[pos:pos + xfade_len] = (
                    out[pos:pos + xfade_len] * r_dn + enhanced_slice[:xfade_len] * r_up
                )
            # Copy non-overlapping tail
            tail_start = xfade_len
            tail_end = slice_len
            if tail_start < tail_end:
                out[pos + tail_start:pos + tail_end] = enhanced_slice[tail_start:tail_end]

        if end >= n:
            break
        pos += hop

    return out


def denoise_audio(
    input_16k_path: str,
    output_16k_path: str,
    *,
    model_name: str = "DeepFilterNet3",
    device: Optional[str] = None,
    chunk_sec: Optional[float] = None,
    overlap_sec: Optional[float] = None,
) -> Tuple[str, list]:
    """
    Denoise via DeepFilterNet at 48 kHz, save 16 kHz clean WAV.
    Returns (output_path, handles_to_flush).

    Audio is processed in overlapping windows to bound VRAM usage.
    chunk_sec / overlap_sec override the module-level defaults.
    """
    device_str = device or ("cuda" if torch.cuda.is_available() else "cpu")
    torch_device = torch.device(device_str)

    _chunk_sec = chunk_sec if chunk_sec is not None else DENOISE_CHUNK_SEC
    _overlap_sec = overlap_sec if overlap_sec is not None else DENOISE_OVERLAP_SEC

    from torchaudio_compat import apply_deepfilter_compat

    apply_deepfilter_compat()
    import importlib

    df_enhance_mod = importlib.import_module("df.enhance")
    enhance = df_enhance_mod.enhance
    init_df = df_enhance_mod.init_df

    model_base_dir = _deepfilter_model_dir(model_name)
    model, df_state = None, None
    try:
        if device_str != "cpu":
            # Force the CUDA context and expandable-segments allocator into a fully-initialised
            # state before df.init_df() touches them.  Without this, init_df() triggers lazy
            # CUDA init whose async handle registration races with the first spec.to(device)
            # call inside enhance(), producing either cudaErrorNotReady or
            # !handles_.at(i) INTERNAL ASSERT FAILED (CUDACachingAllocator.cpp:430).
            _w = torch.zeros(1, device=torch_device)
            _w.sum()
            torch.cuda.synchronize()
            del _w
            torch.cuda.empty_cache()

        model, df_state, _ = init_df(default_model=model_name, model_base_dir=model_base_dir)
        if device_str == "cpu":
            model = model.cpu()
        else:
            model = model.to(torch_device)
            torch.cuda.synchronize()

        audio, sr = sf.read(input_16k_path, dtype="float32")
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)
        wav = torch.from_numpy(audio).float()
        if sr != DENOISE_SR:
            wav = _resample(wav.unsqueeze(0), sr, DENOISE_SR).squeeze(0)

        chunk_samples = int(_chunk_sec * DENOISE_SR)
        overlap_samples = int(_overlap_sec * DENOISE_SR)
        min_chunk_samples = int(DENOISE_MIN_CHUNK_SEC * DENOISE_SR)

        # df_features() calls audio.numpy(); waveform must stay on CPU before enhance().
        # For windowed path we pass slices; get_device override only needed on CPU device_str.
        orig_get_device = df_enhance_mod.get_device
        if device_str == "cpu":
            df_enhance_mod.get_device = lambda: torch.device("cpu")
        try:
            with torch.inference_mode():
                enhanced_48k = _enhance_windowed(
                    enhance, model, df_state, wav,
                    chunk_samples, overlap_samples,
                    df_enhance_mod, device_str, torch_device, min_chunk_samples,
                )
        finally:
            df_enhance_mod.get_device = orig_get_device

        enhanced_16k = _resample(enhanced_48k.unsqueeze(0), DENOISE_SR, SAMPLE_RATE).squeeze(0)
        os.makedirs(os.path.dirname(output_16k_path) or ".", exist_ok=True)
        sf.write(output_16k_path, enhanced_16k.numpy(), SAMPLE_RATE, subtype="PCM_16")

        duration_in = len(audio) / float(sr if sr else SAMPLE_RATE)
        duration_out = len(enhanced_16k) / float(SAMPLE_RATE)
        if abs(duration_in - duration_out) > 0.05:
            logger.warning(
                "Denoise duration drift: in=%.3fs out=%.3fs", duration_in, duration_out
            )

        logger.info("Denoise complete: %s", output_16k_path)
        return output_16k_path, []
    finally:
        flush_memory([x for x in [model, df_state] if x is not None])


def passthrough_clean(original_path: str, clean_path: str) -> str:
    """Copy or alias original when denoise is disabled."""
    if os.path.abspath(original_path) == os.path.abspath(clean_path):
        return clean_path
    import shutil

    shutil.copy2(original_path, clean_path)
    return clean_path
