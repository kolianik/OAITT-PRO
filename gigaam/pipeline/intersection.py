"""Step 6: IoW speaker assignment and sentence assembly."""
from __future__ import annotations

import logging
import math
import re
from typing import List, Optional

from .types import DiarizationNode, WordNode

logger = logging.getLogger("gigaam-service.intersection")

IOW_THRESHOLD = 0.5
SENTENCE_END = re.compile(r"[.!?…]\s*$")


def _intersection(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _iow(word: WordNode, seg: DiarizationNode) -> float:
    dur = word.end - word.start
    if dur <= 0:
        return 0.0
    return _intersection(word.start, word.end, seg.start, seg.end) / dur


def _midpoint_in(word: WordNode, seg: DiarizationNode) -> bool:
    mid = (word.start + word.end) / 2.0
    return seg.start <= mid <= seg.end


def _pick_speaker_iow(word: WordNode, diar_segments: List[DiarizationNode]) -> str:
    dur = word.end - word.start
    candidates: List[DiarizationNode] = []

    if dur <= 0:
        for seg in diar_segments:
            if _midpoint_in(word, seg):
                candidates.append(seg)
    else:
        for seg in diar_segments:
            if _iow(word, seg) >= IOW_THRESHOLD or _midpoint_in(word, seg):
                candidates.append(seg)

    if not candidates:
        return "UNKNOWN_SPEAKER"
    if len(candidates) == 1:
        return candidates[0].speaker_id

    best = min(candidates, key=lambda s: s.end - s.start)
    tied = [
        s
        for s in candidates
        if abs((s.end - s.start) - (best.end - best.start)) < 1e-6
    ]
    if len(tied) > 1:
        ids = sorted({s.speaker_id for s in tied})
        return f"UNKNOWN_OVERLAP_{'_'.join(ids)}"
    return best.speaker_id


def assign_speakers(
    words: List[WordNode],
    diar_segments: List[DiarizationNode],
) -> List[WordNode]:
    """IoW + backchannel tie-breaker per word."""
    for word in words:
        word.speaker = _pick_speaker_iow(word, diar_segments)
    return words


def _segment_avg_logprob(words: List[WordNode]) -> float:
    scores = [max(w.score, 1e-6) for w in words if w.score > 0]
    if not scores:
        return 0.0
    return float(math.log(max(min(sum(scores) / len(scores), 1.0), 1e-6)))


def assemble_segments(
    words: List[WordNode],
    *,
    diarize: bool,
) -> List[dict]:
    """Group words into API segments."""
    if not words:
        return []

    segments: List[dict] = []
    current_words: List[WordNode] = []
    current_speaker: Optional[str] = None

    def flush():
        nonlocal current_words, current_speaker
        if not current_words:
            return
        text = " ".join(w.word.strip() for w in current_words if w.word).strip()
        if not text:
            current_words = []
            return
        seg = {
            "id": len(segments),
            "start": current_words[0].start,
            "end": current_words[-1].end,
            "text": text,
            "speaker": current_speaker if diarize else None,
            "avg_logprob": _segment_avg_logprob(current_words),
            "words": [
                {
                    "word": w.word,
                    "start": w.start,
                    "end": w.end,
                    "probability": w.score,
                    "tag": w.tag,
                }
                for w in current_words
            ],
        }
        segments.append(seg)
        current_words = []

    for w in words:
        spk = w.speaker if diarize else None
        boundary = (
            current_words and diarize and spk != current_speaker
        ) or (
            current_words and SENTENCE_END.search(current_words[-1].word or "")
        )
        if boundary:
            flush()
        current_words.append(w)
        current_speaker = spk

    flush()
    return segments
