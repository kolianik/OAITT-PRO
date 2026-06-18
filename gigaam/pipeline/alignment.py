"""Step 4: Chunked Wav2Vec2 CTC forced alignment.

Frames are mapped to words by *target position* via ``torchaudio.functional.merge_tokens``
(one ``TokenSpan`` per target token, in order) — never by token-ID, which would scramble word
order and drop words. Alignment never drops ASR text: out-of-vocabulary words are time-interpolated
between aligned neighbours, and if ``forced_align`` cannot run the chunk's words are distributed
evenly. Word-level confidence filtering is intentionally *not* done here (low-confidence words are
tagged ``low_conf`` and emitted); anti-hallucination filtering lives in the gateway.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf
import torch
import torchaudio
import torchaudio.functional as AF

from .memory import flush_memory
from .model_cache import hub_load_kwargs, is_hub_offline
from .preprocess import SAMPLE_RATE
from .types import AudioChunk, WordNode

logger = logging.getLogger("gigaam-service.alignment")

LOW_CONF_THRESHOLD = 0.60
WORD_PATTERN = re.compile(r"([^\w\s]+)?(\w+)([^\w\s]*)?", re.UNICODE)

# T-008 cluster spreading: pull apart groups of words crammed into < N * gap.
_MIN_WORD_GAP_S = 0.10
_MIN_CLUSTER_SIZE = 3

_align_model = None
_align_processor = None


@dataclass
class _TokenWord:
    letters: str
    leading: str
    trailing: str
    char_start: int
    char_end: int


def _load_aligner(model_name: str, device: torch.device, cache_dir: str):
    global _align_model, _align_processor
    from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

    if _align_model is None:
        logger.info("Loading alignment model %s", model_name)
        kwargs = hub_load_kwargs(cache_dir=cache_dir)
        try:
            _align_processor = Wav2Vec2Processor.from_pretrained(
                model_name, **kwargs
            )
            _align_model = Wav2Vec2ForCTC.from_pretrained(
                model_name, **kwargs
            ).to(device)
        except Exception as exc:
            if is_hub_offline() or kwargs.get("local_files_only"):
                raise
            logger.warning("Alignment load failed (%s), retrying offline", exc)
            offline = hub_load_kwargs(cache_dir=cache_dir, local_only=True)
            _align_processor = Wav2Vec2Processor.from_pretrained(
                model_name, **offline
            )
            _align_model = Wav2Vec2ForCTC.from_pretrained(
                model_name, **offline
            ).to(device)
        if device.type == "cuda":
            _align_model = _align_model.half()
        _align_model.eval()
    return _align_model, _align_processor


def _parse_text(text: str) -> List[_TokenWord]:
    words: List[_TokenWord] = []
    pos = 0
    for m in WORD_PATTERN.finditer(text):
        leading = m.group(1) or ""
        letters = m.group(2) or ""
        trailing = m.group(3) or ""
        if not letters:
            continue
        start = m.start(2)
        end = m.end(2)
        words.append(
            _TokenWord(
                letters=letters,
                leading=leading,
                trailing=trailing,
                char_start=start,
                char_end=end,
            )
        )
        pos = end
    if not words and text.strip():
        words.append(_TokenWord(text.strip(), "", "", 0, len(text)))
    return words


def _chars_in_vocab(processor, word: str) -> bool:
    vocab = processor.tokenizer.get_vocab()
    lowered = word.lower()
    return all(ch in vocab for ch in lowered)


def _build_char_targets(
    processor, token_words: List[_TokenWord]
) -> Tuple[List[int], List[Tuple[int, int]]]:
    """Return flat char token ids and (start,end) index per word in char sequence."""
    ids: List[int] = []
    spans: List[Tuple[int, int]] = []
    vocab = processor.tokenizer.get_vocab()
    for tw in token_words:
        start = len(ids)
        for ch in tw.letters.lower():
            if ch in vocab:
                ids.append(vocab[ch])
        end = len(ids)
        spans.append((start, end))
    return ids, spans


def _make_node(tw: _TokenWord, start: float, end: float, score: float,
               tag: Optional[str]) -> WordNode:
    return WordNode(
        word=tw.leading + tw.letters + tw.trailing,
        start=start,
        end=end,
        score=score,
        tag=tag,
        leading_punct=tw.leading,
        trailing_punct=tw.trailing,
    )


def _evenly_distribute(token_words: List[_TokenWord], dur: float) -> List[WordNode]:
    """No-loss fallback: spread every word evenly over [0, dur] when alignment is unavailable."""
    n = len(token_words)
    if n == 0:
        return []
    dur = max(dur, 0.0)
    step = dur / n if n else 0.0
    out: List[WordNode] = []
    for i, tw in enumerate(token_words):
        start = i * step
        end = min((i + 1) * step, dur) if dur > 0 else start
        out.append(_make_node(tw, start, max(end, start), 0.5, "unaligned"))
    return out


def _map_alignable(
    alignable: List[Tuple[int, _TokenWord]],
    spans: List[Tuple[int, int]],
    token_spans,
    frame_dur: float,
    audio_dur: float,
    low_conf: float = LOW_CONF_THRESHOLD,
) -> Dict[int, WordNode]:
    """Map each alignable word to a WordNode via its target-position span. Keyed by original index."""
    anchors: Dict[int, WordNode] = {}
    for (orig_idx, tw), (cs, ce) in zip(alignable, spans):
        if cs >= ce:
            continue
        word_spans = token_spans[cs:ce]
        if not word_spans:
            continue
        start_t = float(word_spans[0].start) * frame_dur
        end_t = min(float(word_spans[-1].end) * frame_dur, audio_dur)
        if end_t < start_t:
            end_t = start_t
        score = float(sum(float(s.score) for s in word_spans) / len(word_spans))
        score = max(0.0, min(score, 1.0))
        tag = "low_conf" if score < low_conf else None
        anchors[orig_idx] = _make_node(tw, start_t, end_t, score, tag)
    return anchors


def _interpolate_oov(
    oov_idx: List[int],
    token_words: List[_TokenWord],
    anchors: Dict[int, WordNode],
    audio_dur: float,
) -> Dict[int, WordNode]:
    """Place OOV/unmapped words between their nearest aligned anchors (even split within a gap)."""
    if not oov_idx:
        return {}
    oov_sorted = sorted(oov_idx)
    anchor_keys = sorted(anchors.keys())
    result: Dict[int, WordNode] = {}

    # Group consecutive original indices into runs sharing the same anchor gap.
    runs: List[List[int]] = []
    run = [oov_sorted[0]]
    for k in oov_sorted[1:]:
        if k == run[-1] + 1:
            run.append(k)
        else:
            runs.append(run)
            run = [k]
    runs.append(run)

    for run in runs:
        lo, hi = run[0], run[-1]
        prev = [a for a in anchor_keys if a < lo]
        nxt = [a for a in anchor_keys if a > hi]
        prev_end = anchors[prev[-1]].end if prev else 0.0
        next_start = anchors[nxt[0]].start if nxt else audio_dur
        if next_start < prev_end:
            next_start = prev_end
        gap = next_start - prev_end
        step = gap / (len(run) + 1) if gap > 0 else 0.0
        for r, idx in enumerate(run):
            t = prev_end + step * (r + 1)
            result[idx] = _make_node(token_words[idx], t, t, 0.5, "unaligned")
    return result


def _spread_clusters(words: List[WordNode], chunk_dur: float) -> List[WordNode]:
    """Pull apart groups of >= _MIN_CLUSTER_SIZE words crammed into < N * _MIN_WORD_GAP_S (safety net)."""
    if len(words) < _MIN_CLUSTER_SIZE:
        return words
    i = 0
    while i < len(words):
        j = i + 1
        while j < len(words) and (words[j].start - words[i].start) < (j - i) * _MIN_WORD_GAP_S:
            j += 1
        cluster_size = j - i
        if cluster_size >= _MIN_CLUSTER_SIZE:
            cluster_start = words[i].start
            cluster_end = words[j].start if j < len(words) else chunk_dur
            avail = max(cluster_end - cluster_start, cluster_size * _MIN_WORD_GAP_S)
            step = avail / cluster_size
            for k in range(cluster_size):
                w = words[i + k]
                w.start = cluster_start + k * step
                if w.end < w.start:
                    w.end = min(w.start + step, chunk_dur)
            i = j
        else:
            i += 1
    return words


def align_chunk(
    chunk: AudioChunk,
    *,
    model_name: str,
    device: torch.device,
    cache_dir: str,
) -> List[WordNode]:
    """Align a single chunk; returns local-time WordNodes in original (spoken) order."""
    if not chunk.text or not chunk.text.strip():
        return []

    model, processor = _load_aligner(model_name, device, cache_dir)
    audio, sr = sf.read(chunk.file_path, dtype="float32")
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    if sr != SAMPLE_RATE:
        wav = torch.from_numpy(audio).float().unsqueeze(0)
        wav = torchaudio.functional.resample(wav, sr, SAMPLE_RATE)
        audio = wav.squeeze(0).numpy()
    audio_dur = len(audio) / SAMPLE_RATE

    token_words = _parse_text(chunk.text)
    if not token_words:
        return []

    # Classify OOV vs alignable words, preserving original order/index.
    oov_idx: List[int] = []
    alignable: List[Tuple[int, _TokenWord]] = []
    for i, tw in enumerate(token_words):
        if _chars_in_vocab(processor, tw.letters):
            alignable.append((i, tw))
        else:
            oov_idx.append(i)

    if not alignable:
        oov_nodes = _interpolate_oov(oov_idx, token_words, {}, audio_dur)
        return [oov_nodes[i] for i in range(len(token_words)) if i in oov_nodes]

    target_ids, spans = _build_char_targets(processor, [tw for _, tw in alignable])
    if not target_ids:
        return _evenly_distribute(token_words, audio_dur)

    inputs = processor(
        audio, sampling_rate=SAMPLE_RATE, return_tensors="pt", padding=True
    )
    input_values = inputs.input_values.to(device)
    if device.type == "cuda":
        input_values = input_values.half()

    with torch.inference_mode():
        logits = model(input_values).logits.float()
    log_probs = torch.log_softmax(logits, dim=-1)
    input_lengths = torch.tensor([log_probs.shape[1]], device=device)
    targets = torch.tensor([target_ids], dtype=torch.long, device=device)
    target_lengths = torch.tensor([len(target_ids)], device=device)

    t_len = log_probs.shape[1]
    frame_dur = audio_dur / max(t_len, 1)

    try:
        paths, scores = AF.forced_align(
            log_probs, targets, input_lengths, target_lengths, blank=0
        )
    except Exception as exc:
        logger.warning(
            "forced_align failed for chunk %s (%s); distributing %d word(s) evenly",
            chunk.chunk_id, exc, len(token_words),
        )
        return _evenly_distribute(token_words, audio_dur)

    aligned = paths[0].cpu()
    sc = scores[0].cpu().exp()
    try:
        token_spans = AF.merge_tokens(aligned, sc, blank=0)
    except Exception as exc:
        logger.warning(
            "merge_tokens failed for chunk %s (%s); distributing %d word(s) evenly",
            chunk.chunk_id, exc, len(token_words),
        )
        return _evenly_distribute(token_words, audio_dur)

    if len(token_spans) != len(target_ids):
        logger.warning(
            "merge_tokens len %d != targets %d (chunk %s); distributing evenly",
            len(token_spans), len(target_ids), chunk.chunk_id,
        )
        return _evenly_distribute(token_words, audio_dur)

    anchors = _map_alignable(alignable, spans, token_spans, frame_dur, audio_dur)

    # Any alignable word that failed to map joins the OOV words for interpolation (never dropped).
    mapped = set(anchors.keys())
    interp_targets = sorted(oov_idx + [i for (i, _) in alignable if i not in mapped])
    oov_nodes = _interpolate_oov(interp_targets, token_words, anchors, audio_dur)

    all_nodes: Dict[int, WordNode] = {}
    all_nodes.update(anchors)
    all_nodes.update(oov_nodes)
    return [all_nodes[i] for i in range(len(token_words)) if i in all_nodes]


def align_chunks(
    chunks: List[AudioChunk],
    *,
    model_name: str,
    device: torch.device,
    cache_dir: str,
) -> List[WordNode]:
    """Iterative per-chunk alignment with global time offset."""
    global _align_model, _align_processor
    all_words: List[WordNode] = []

    for chunk in chunks:
        local = align_chunk(
            chunk, model_name=model_name, device=device, cache_dir=cache_dir
        )
        local = _spread_clusters(local, chunk.end_time - chunk.start_time)
        for w in local:
            w.start += chunk.start_time
            w.end += chunk.start_time
            all_words.append(w)
        flush_memory([local])

    if _align_model is not None:
        flush_memory([_align_model, _align_processor])
        _align_model = None
        _align_processor = None

    logger.info("Alignment produced %d words from %d chunks", len(all_words), len(chunks))
    return all_words
