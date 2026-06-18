"""Tests for alignment frame->word mapping, no-loss guarantees, OOV interpolation, cluster spreading.

These cover the root-cause fix for the bug where align_chunk mapped frames to words by comparing
forced_align token-IDs against character positions, which both scrambled word order and silently
dropped every word past ~the vocab size per chunk.
"""
import os
import sys
from collections import namedtuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam")))

from pipeline.alignment import (  # noqa: E402
    LOW_CONF_THRESHOLD,
    _MIN_WORD_GAP_S,
    _build_char_targets,
    _evenly_distribute,
    _interpolate_oov,
    _map_alignable,
    _parse_text,
    _spread_clusters,
)
from pipeline.types import WordNode  # noqa: E402

# Minimal stand-in for torchaudio.functional.merge_tokens' TokenSpan (only fields we read).
FakeSpan = namedtuple("FakeSpan", ["start", "end", "score"])


class _FakeVocabProcessor:
    """A processor whose tokenizer maps lowercase Cyrillic chars to ids; everything else is OOV."""

    def __init__(self):
        chars = " абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
        self._vocab = {ch: i for i, ch in enumerate(chars)}

        class _Tok:
            def get_vocab(_self):
                return self._vocab

        self.tokenizer = _Tok()


def _alignable_and_spans(text, processor):
    """Replicate align_chunk's classification for tests, returning (alignable, spans)."""
    from pipeline.alignment import _chars_in_vocab

    token_words = _parse_text(text)
    alignable = [
        (i, tw) for i, tw in enumerate(token_words) if _chars_in_vocab(processor, tw.letters)
    ]
    target_ids, spans = _build_char_targets(processor, [tw for _, tw in alignable])
    return token_words, alignable, target_ids, spans


def test_parse_text_splits_punctuation():
    tokens = _parse_text("Привет, мир!")
    assert len(tokens) >= 2
    assert tokens[0].letters.lower() == "привет"
    assert tokens[0].trailing == ","


def test_map_alignable_preserves_order():
    proc = _FakeVocabProcessor()
    token_words, alignable, target_ids, spans = _alignable_and_spans("случае влияние в", proc)
    assert len(alignable) == 3
    # One span per char, strictly increasing frames -> monotonic alignment.
    token_spans = [FakeSpan(start=k, end=k + 1, score=0.9) for k in range(len(target_ids))]

    anchors = _map_alignable(alignable, spans, token_spans, frame_dur=0.02, audio_dur=2.0)

    assert set(anchors.keys()) == {0, 1, 2}
    nodes = [anchors[i] for i in range(3)]
    assert [n.word for n in nodes] == ["случае", "влияние", "в"]
    starts = [n.start for n in nodes]
    assert starts == sorted(starts)  # NOT scrambled
    for n in nodes:
        assert n.start <= n.end


def test_no_missing_words_past_vocab_size():
    """The bug dropped every word whose char position exceeded the vocab size. All must survive."""
    proc = _FakeVocabProcessor()
    text = " ".join(["слово"] * 12)  # 60 chars, far past the ~33-id fake vocab
    token_words, alignable, target_ids, spans = _alignable_and_spans(text, proc)
    token_spans = [FakeSpan(start=k, end=k + 1, score=0.9) for k in range(len(target_ids))]

    anchors = _map_alignable(alignable, spans, token_spans, frame_dur=0.02, audio_dur=6.0)

    assert len(anchors) == len(token_words) == 12  # nothing dropped


def test_evenly_distribute_keeps_all_words():
    words = _parse_text("раз два три четыре")
    nodes = _evenly_distribute(words, 4.0)
    assert [n.word for n in nodes] == ["раз", "два", "три", "четыре"]
    starts = [n.start for n in nodes]
    assert starts == sorted(starts)
    for n in nodes:
        assert n.start <= n.end <= 4.0 + 1e-9
        assert n.tag == "unaligned"


def test_oov_interpolated_between_neighbors():
    words = _parse_text("слово B2B- слово")
    assert len(words) == 3  # слово / B2B- / слово
    anchors = {
        0: WordNode("слово", 0.0, 1.0, 0.9),
        2: WordNode("слово", 2.0, 3.0, 0.9),
    }
    oov = _interpolate_oov([1], words, anchors, audio_dur=3.0)
    assert 1 in oov
    node = oov[1]
    assert anchors[0].end < node.start < anchors[2].start  # strictly between, not chunk start
    assert node.tag == "unaligned"
    assert "B2B" in node.word


def test_oov_only_chunk_distributes_across_audio():
    words = _parse_text("B2B- ЦПР ЕКМП")
    oov = _interpolate_oov([0, 1, 2], words, {}, audio_dur=3.0)
    assert set(oov.keys()) == {0, 1, 2}
    starts = [oov[i].start for i in range(3)]
    assert starts == sorted(starts)
    assert 0.0 <= starts[0] and starts[-1] <= 3.0


def test_spread_clusters_separates_coincident_starts():
    words = [WordNode(f"w{i}", 1.0, 1.0, 0.9) for i in range(5)]
    out = _spread_clusters(words, 10.0)
    starts = [w.start for w in out]
    for a, b in zip(starts, starts[1:]):
        assert b - a >= _MIN_WORD_GAP_S - 1e-9
    for w in out:
        assert w.end >= w.start


def test_spread_clusters_leaves_well_spaced_words():
    words = [WordNode(f"w{i}", float(i), float(i) + 0.5, 0.9) for i in range(5)]
    before = [w.start for w in words]
    out = _spread_clusters(words, 10.0)
    assert [w.start for w in out] == before  # untouched


def test_merge_tokens_invariant_with_real_torchaudio():
    """The core fix assumes len(merge_tokens(...)) == len(target_ids), incl. repeated chars."""
    import torch
    import torchaudio.functional as AF

    # Two "words": ids [1,2,2] and [3,1] -> 5 target tokens, with an adjacent repeat (2,2).
    target_ids = [1, 2, 2, 3, 1]
    spans = [(0, 3), (3, 5)]
    vocab_size = 5
    t_len = 16
    # Uniform emission: forced_align still aligns the *given* targets monotonically.
    log_probs = torch.full((1, t_len, vocab_size), 0.0).log_softmax(dim=-1)
    targets = torch.tensor([target_ids], dtype=torch.long)
    paths, scores = AF.forced_align(
        log_probs,
        targets,
        torch.tensor([t_len]),
        torch.tensor([len(target_ids)]),
        blank=0,
    )
    token_spans = AF.merge_tokens(paths[0].cpu(), scores[0].cpu().exp(), blank=0)
    assert len(token_spans) == len(target_ids)  # the invariant the code guards on

    # Two fake alignable words sliced by their char spans stay in order with start < end frames.
    TW = _parse_text("аа ббб")  # placeholder words, only used for WordNode text
    alignable = [(0, TW[0]), (1, TW[1])]
    anchors = _map_alignable(alignable, spans, token_spans, frame_dur=0.02, audio_dur=t_len * 0.02)
    assert set(anchors.keys()) == {0, 1}
    assert anchors[0].start <= anchors[1].start


def test_low_conf_tag_applied_not_dropped():
    proc = _FakeVocabProcessor()
    token_words, alignable, target_ids, spans = _alignable_and_spans("случае", proc)
    low = LOW_CONF_THRESHOLD - 0.1
    token_spans = [FakeSpan(start=k, end=k + 1, score=low) for k in range(len(target_ids))]
    anchors = _map_alignable(alignable, spans, token_spans, frame_dur=0.02, audio_dur=1.0)
    assert len(anchors) == 1  # emitted, not removed
    assert anchors[0].tag == "low_conf"
