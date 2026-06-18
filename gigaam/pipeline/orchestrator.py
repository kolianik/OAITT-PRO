"""Pipeline orchestrator: split-path, sequential GPU stages."""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from typing import Optional

import soundfile as sf
import torch

from .alignment import align_chunks
from .asr_onnx import transcribe_chunks
from .denoise import denoise_audio, passthrough_clean
from .diarization import run_diarization
from .intersection import assemble_segments, assign_speakers
from .preprocess import SAMPLE_RATE, preprocess_audio
from .types import AudioChunk, WordNode
from .vad_chunking import chunk_audio

logger = logging.getLogger("gigaam-service.orchestrator")


def _audio_duration(path: str) -> float:
    with sf.SoundFile(path) as f:
        return f.frames / float(f.samplerate)


def _fallback_segments_from_chunks(chunks: list[AudioChunk], diarize: bool) -> list[dict]:
    segments = []
    for c in chunks:
        if not c.text:
            continue
        segments.append(
            {
                "id": len(segments),
                "start": c.start_time,
                "end": c.end_time,
                "text": c.text,
                "speaker": None if not diarize else "UNKNOWN_SPEAKER",
                "avg_logprob": 0.0,
                "words": [],
            }
        )
    return segments


def run_pipeline(
    file_path: str,
    *,
    diarize: bool = False,
    language: Optional[str] = None,
    onnx_dir: str = "/app/data/gigaam_onnx",
    model_version: str = "v3_e2e_rnnt",
    align_model: str = "jonatasgrosman/wav2vec2-xls-r-1b-russian",
    cache_dir: str = "/app/data",
    hf_token: str = "",
    batch_size: int = 4,
    diarize_batch_size: int = 8,
    denoise_enabled: bool = True,
    denoise_model: str = "DeepFilterNet3",
    denoise_device: Optional[str] = None,
    denoise_chunk_sec: Optional[float] = None,
    denoise_overlap_sec: Optional[float] = None,
) -> dict:
    """Execute full GigaAM pipeline and return gateway-compatible JSON."""
    work_dir = tempfile.mkdtemp(prefix="gigaam_")
    try:
        original_wav = os.path.join(work_dir, "audio_16k_mono.wav")
        clean_wav = os.path.join(work_dir, "audio_clean.wav")
        chunks_dir = os.path.join(work_dir, "chunks")

        preprocess_audio(file_path, original_wav)
        duration = _audio_duration(original_wav)
        logger.info(
            "Pipeline start: %.1fs audio (diarize=%s, denoise=%s)",
            duration,
            diarize,
            denoise_enabled,
        )

        if denoise_enabled:
            denoise_audio(
                original_wav,
                clean_wav,
                model_name=denoise_model,
                device=denoise_device,
                chunk_sec=denoise_chunk_sec,
                overlap_sec=denoise_overlap_sec,
            )
        else:
            passthrough_clean(original_wav, clean_wav)

        diar_nodes = []
        if diarize:
            diar_nodes = run_diarization(
                original_wav,
                hf_token=hf_token,
                cache_dir=cache_dir,
                diarize_batch_size=diarize_batch_size,
            )

        chunks = chunk_audio(clean_wav, chunks_dir)
        logger.info("VAD produced %d chunk(s)", len(chunks))
        if not chunks:
            return {
                "text": "",
                "duration": duration,
                "language": "ru",
                "segments": [],
            }

        chunks = transcribe_chunks(
            chunks,
            onnx_dir=onnx_dir,
            model_version=model_version,
            batch_size=batch_size,
        )

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        words: list[WordNode] = align_chunks(
            chunks,
            model_name=align_model,
            device=device,
            cache_dir=cache_dir,
        )
        logger.info("ASR + alignment produced %d word(s)", len(words))

        if words and diarize:
            assign_speakers(words, diar_nodes)
            segments = assemble_segments(words, diarize=True)
        elif words:
            segments = assemble_segments(words, diarize=False)
        else:
            segments = _fallback_segments_from_chunks(chunks, diarize)

        text = " ".join(s.get("text", "").strip() for s in segments).strip()
        return {
            "text": text,
            "duration": duration,
            "language": language or "ru",
            "segments": segments,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
