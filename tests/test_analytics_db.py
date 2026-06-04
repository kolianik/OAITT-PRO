import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gateway")))

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from gateway.main import _parse_date_param as parse_date_main, _validate_date_range
from fastapi import HTTPException
import pytest

from gateway.db import build_cost_from_aggregates


def test_build_cost_no_tariff():
    cost = build_cost_from_aggregates(10.0, 8.33, 0.0, 0.0)
    assert cost["currency"] == "RUB"
    assert cost["audio_minutes"] == 10.0
    assert cost["speech_minutes"] == 8.33
    assert cost["pricing_options"] == {}


def test_build_cost_audio_only_rate():
    cost = build_cost_from_aggregates(10.0, 8.0, 3.0, 0.0, audio_rate=0.3)
    assert "by_audio_duration" in cost["pricing_options"]
    assert "by_speech_duration" not in cost["pricing_options"]
    assert cost["pricing_options"]["by_audio_duration"]["total"] == 3.0
    assert cost["pricing_options"]["by_audio_duration"]["price_per_minute"] == 0.3


def test_build_cost_both_rates():
    cost = build_cost_from_aggregates(10.0, 8.0, 3.0, 3.2, audio_rate=0.3, speech_rate=0.4)
    assert "by_audio_duration" in cost["pricing_options"]
    assert "by_speech_duration" in cost["pricing_options"]
    assert cost["pricing_options"]["by_speech_duration"]["total"] == 3.2


def test_build_cost_multiple_rates_total_only():
    cost = build_cost_from_aggregates(10.0, 8.0, 5.5, 4.0)
    assert cost["pricing_options"]["by_audio_duration"] == {"total": 5.5}
    assert cost["pricing_options"]["by_speech_duration"] == {"total": 4.0}
    assert "price_per_minute" not in cost["pricing_options"]["by_audio_duration"]


def test_build_cost_rounding():
    cost = build_cost_from_aggregates(1.234, 2.345, 0.3702, 0.938, audio_rate=0.3)
    assert cost["audio_minutes"] == 1.23
    assert cost["speech_minutes"] == 2.35
    assert cost["pricing_options"]["by_audio_duration"]["total"] == 0.37


def test_parse_date_valid():
    assert parse_date_main("2026-05-01", "from") == "2026-05-01"


def test_parse_date_invalid():
    with pytest.raises(HTTPException) as exc:
        parse_date_main("05-01-2026", "from")
    assert exc.value.status_code == 400


def test_validate_date_range_invalid():
    with pytest.raises(HTTPException) as exc:
        _validate_date_range("2026-06-01", "2026-05-01")
    assert exc.value.status_code == 400
