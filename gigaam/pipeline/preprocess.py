"""Step 1: ffmpeg preprocessing to 16 kHz mono PCM."""
from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger("gigaam-service.preprocess")

SAMPLE_RATE = 16000


def preprocess_audio(input_path: str, output_path: str) -> str:
    """Convert arbitrary audio to 16 kHz mono s16le WAV via ffmpeg."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        "1",
        "-sample_fmt",
        "s16",
        output_path,
    ]
    logger.info("ffmpeg preprocess: %s -> %s", input_path, output_path)
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        stderr_text = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        logger.error(
            "ffmpeg failed (exit %d) for %s: %s",
            proc.returncode,
            input_path,
            stderr_text[-2000:],
        )
        raise RuntimeError(
            f"ffmpeg preprocessing failed (exit {proc.returncode}): {stderr_text[-500:]}"
        )
    return output_path
