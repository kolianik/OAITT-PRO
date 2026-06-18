"""Tests for IoW speaker assignment and backchannel tie-breaker."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam")))

from pipeline.intersection import assign_speakers, assemble_segments, _iow, _pick_speaker_iow
from pipeline.types import DiarizationNode, WordNode


def test_iow_short_word_in_long_segment():
    word = WordNode("да", 10.0, 10.3, 0.9)
    long_seg = DiarizationNode("SPEAKER_00", 0.0, 60.0)
    assert _iow(word, long_seg) > 0.5


def test_backchannel_tiebreaker_prefers_shorter_segment():
    word = WordNode("да", 10.0, 10.2, 0.85)
    diar = [
        DiarizationNode("SPEAKER_00", 0.0, 60.0),
        DiarizationNode("SPEAKER_01", 10.0, 10.5),
    ]
    spk = _pick_speaker_iow(word, diar)
    assert spk == "SPEAKER_01"


def test_unknown_speaker_when_no_overlap():
    word = WordNode("тест", 5.0, 5.5, 0.9)
    diar = [DiarizationNode("SPEAKER_00", 20.0, 30.0)]
    spk = _pick_speaker_iow(word, diar)
    assert spk == "UNKNOWN_SPEAKER"


def test_assemble_segments_groups_by_speaker():
    words = [
        WordNode("привет", 0.0, 0.5, 0.9, speaker="SPEAKER_00"),
        WordNode("мир", 0.6, 1.0, 0.9, speaker="SPEAKER_00"),
        WordNode("да", 1.1, 1.3, 0.8, speaker="SPEAKER_01"),
    ]
    segs = assemble_segments(words, diarize=True)
    assert len(segs) == 2
    assert segs[0]["speaker"] == "SPEAKER_00"
    assert segs[1]["speaker"] == "SPEAKER_01"


def test_diarize_false_null_speaker():
    words = [WordNode("тест", 0.0, 0.5, 0.9)]
    segs = assemble_segments(words, diarize=False)
    assert segs[0]["speaker"] is None
