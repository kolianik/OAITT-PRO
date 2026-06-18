"""Step 5: Pyannote speaker diarization on original audio."""
from __future__ import annotations

import logging
import os
from typing import List, Optional

import torch

from .memory import flush_memory
from .model_cache import is_hub_offline
from .types import DiarizationNode

logger = logging.getLogger("gigaam-service.diarization")

_pipeline = None
PYANNOTE_MODEL = "pyannote/speaker-diarization-community-1"


def _apply_segmentation_batch_size(pipeline, batch_size: Optional[int]) -> None:
    """Cap Pyannote segmentation peak VRAM (T-002, RTX 3060 6 GB).

    Best-effort: if the pipeline doesn't expose ``_segmentation.batch_size``
    (e.g. a future pyannote rename), the job still runs at the default batch
    size and we log a warning instead of failing the request.
    """
    if not batch_size or batch_size < 1:
        return
    seg = getattr(pipeline, "_segmentation", None)
    if seg is not None and hasattr(seg, "batch_size"):
        seg.batch_size = int(batch_size)
        logger.info("Diarization segmentation batch_size=%d (VRAM cap)", int(batch_size))
    else:
        logger.warning(
            "Pyannote pipeline exposes no _segmentation.batch_size; "
            "GIGAAM_DIARIZE_BATCH_SIZE=%s not applied",
            batch_size,
        )


def run_diarization(
    original_wav_path: str,
    *,
    hf_token: str,
    device: Optional[torch.device] = None,
    cache_dir: Optional[str] = None,
    diarize_batch_size: Optional[int] = None,
) -> List[DiarizationNode]:
    """Diarize original 16 kHz audio (not denoised)."""
    global _pipeline

    if not hf_token:
        raise ValueError(
            "HF_TOKEN is required for Pyannote diarization. "
            "Prefetch the pipeline at bootstrap or set HF_TOKEN in .env."
        )

    if device is None:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    cache = cache_dir or os.environ.get("HF_HOME", "/app/data")
    os.environ.setdefault("HF_HOME", cache)
    from pyannote.audio import Pipeline

    if _pipeline is None:
        logger.info("Loading Pyannote diarization pipeline...")
        try:
            _pipeline = Pipeline.from_pretrained(
                PYANNOTE_MODEL,
                token=hf_token,
            )
        except Exception as exc:
            if is_hub_offline():
                raise RuntimeError(
                    "Pyannote pipeline not found in local cache. "
                    "Run bootstrap with HF_TOKEN or import a seeded volume."
                ) from exc
            raise
        _pipeline.to(device)
        _apply_segmentation_batch_size(_pipeline, diarize_batch_size)

        # Apply tunable params only if present (H2)
        try:
            params = _pipeline.parameters(instantiated=True)
            updates = {}
            if "segmentation" in params and "min_duration_off" in params["segmentation"]:
                updates.setdefault("segmentation", {})["min_duration_off"] = 0.5
            if updates:
                _pipeline.instantiate(updates)
        except Exception as exc:
            logger.debug("Pyannote param tuning skipped: %s", exc)

    result = _pipeline(original_wav_path)
    annotation = (
        result.speaker_diarization
        if hasattr(result, "speaker_diarization")
        else result
    )

    nodes: List[DiarizationNode] = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        nodes.append(
            DiarizationNode(
                speaker_id=str(speaker),
                start=float(turn.start),
                end=float(turn.end),
            )
        )

    flush_memory([_pipeline])
    _pipeline = None

    logger.info("Diarization: %d speaker segments", len(nodes))
    return nodes


def reset_diarization_pipeline() -> None:
    global _pipeline
    _pipeline = None
