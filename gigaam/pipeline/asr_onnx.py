"""Step 3: GigaAM ONNX Runtime GPU batch ASR."""
from __future__ import annotations

import logging
import os
from typing import List, Optional, Tuple

from .memory import flush_memory
from .types import AudioChunk

logger = logging.getLogger("gigaam-service.asr")

_sessions = None
_model_cfg = None


def transcribe_chunks(
    chunks: List[AudioChunk],
    *,
    onnx_dir: str,
    model_version: str = "v3_e2e_rnnt",
    batch_size: int = 4,
    provider: Optional[str] = None,
) -> List[AudioChunk]:
    """Run ONNX ASR on chunk WAV paths; fills chunk.text."""
    global _sessions, _model_cfg

    from gigaam.onnx_utils import infer_onnx, load_onnx

    if provider is None:
        provider = "CUDAExecutionProvider"

    onnx_path = os.path.join(onnx_dir, model_version)
    if not os.path.isdir(onnx_dir) and os.path.isdir(onnx_path):
        onnx_dir = onnx_path

    sessions, model_cfg = load_onnx(onnx_dir, model_version, provider=provider)
    paths = [c.file_path for c in chunks]

    texts = infer_onnx(
        paths,
        model_cfg,
        sessions,
        batch_size=batch_size,
        progress=False,
    )

    for chunk, text in zip(chunks, texts):
        chunk.text = (text or "").strip()

    flush_memory([sessions, model_cfg])
    _sessions = None
    _model_cfg = None

    logger.info("ASR transcribed %d chunks", len(chunks))
    return chunks
