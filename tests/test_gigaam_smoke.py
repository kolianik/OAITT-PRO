"""Unit tests for the GigaAM smoke-test result-shape validator.

Pins the GigaAM result contract from API_transcriptions.md so the committed
verification logic fails if the response shape drifts.
"""
from __future__ import annotations

import os
import sys

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPTS)

from gigaam_smoke import validate_transcription_shape  # noqa: E402


def _diarized_payload() -> dict:
    return {
        "text": "привет как дела",
        "language": "ru",
        "duration": 3.0,
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 1.5,
                "text": "привет",
                "speaker": "SPEAKER_00",
                "avg_logprob": -0.12,
                "words": [{"word": "привет", "start": 0.1, "end": 0.5, "probability": 0.9, "tag": None}],
            },
            {
                "id": 1,
                "start": 1.6,
                "end": 3.0,
                "text": "как дела",
                "speaker": "SPEAKER_01",
                "avg_logprob": -0.2,
            },
        ],
    }


def test_valid_diarized_payload():
    assert validate_transcription_shape(_diarized_payload(), expect_diarization=True) == []


def test_valid_without_diarization_null_speaker_ok():
    payload = _diarized_payload()
    for seg in payload["segments"]:
        seg["speaker"] = None
    assert validate_transcription_shape(payload, expect_diarization=False) == []


def test_diarize_expected_but_no_speaker_flags_problem():
    payload = _diarized_payload()
    for seg in payload["segments"]:
        seg["speaker"] = None
    problems = validate_transcription_shape(payload, expect_diarization=True)
    assert any("speaker" in p for p in problems)


def test_missing_segments_flagged():
    problems = validate_transcription_shape({"text": "x", "duration": 1.0}, expect_diarization=False)
    assert any("segments" in p for p in problems)


def test_missing_text_and_duration_flagged():
    problems = validate_transcription_shape({"segments": []}, expect_diarization=False)
    assert any("text" in p for p in problems)
    assert any("duration" in p for p in problems)


def test_malformed_word_flagged():
    payload = _diarized_payload()
    payload["segments"][0]["words"] = [{"start": 0.0}]  # no "word" key
    problems = validate_transcription_shape(payload, expect_diarization=True)
    assert any("word" in p for p in problems)


def test_non_object_result_flagged():
    assert validate_transcription_shape("nope", expect_diarization=False) == ["result is not a JSON object"]
