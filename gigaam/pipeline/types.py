"""Shared data types for the GigaAM transcription pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class AudioChunk:
    chunk_id: int
    start_time: float
    end_time: float
    file_path: str
    text: str = ""


@dataclass
class WordNode:
    word: str
    start: float
    end: float
    score: float
    tag: Optional[str] = None
    leading_punct: str = ""
    trailing_punct: str = ""
    speaker: Optional[str] = None


@dataclass
class DiarizationNode:
    speaker_id: str
    start: float
    end: float


@dataclass
class PipelinePaths:
    work_dir: str
    original_wav: str
    clean_wav: str
