"""GigaAM bootstrap retry/backoff contract (agents.md §2.5 "Model cache").

Each download step retries transient failures with exponential backoff before the
bootstrap attempt ends in ``failed``. A persistent failure still raises (so the
service reports ``failed`` and a restart re-runs bootstrap idempotently).
"""
from __future__ import annotations

import os
import sys

import pytest

GIGAAM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam"))
sys.path.insert(0, GIGAAM_ROOT)

import bootstrap_models  # noqa: E402


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(bootstrap_models.time, "sleep", lambda *_a, **_k: None)


def test_retry_succeeds_after_transient_failures():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "ok"

    assert bootstrap_models._retry(flaky, attempts=3, base_delay=0.0, label="x") == "ok"
    assert calls["n"] == 3


def test_retry_raises_after_exhausting():
    calls = {"n": 0}

    def always():
        calls["n"] += 1
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        bootstrap_models._retry(always, attempts=3, base_delay=0.0, label="x")
    assert calls["n"] == 3


def test_retry_single_attempt_does_not_retry():
    calls = {"n": 0}

    def always():
        calls["n"] += 1
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        bootstrap_models._retry(always, attempts=1, base_delay=0.0, label="x")
    assert calls["n"] == 1


def test_load_config_reads_retry_env(monkeypatch):
    monkeypatch.setenv("GIGAAM_BOOTSTRAP_RETRIES", "5")
    monkeypatch.setenv("GIGAAM_BOOTSTRAP_RETRY_DELAY", "2.5")
    cfg = bootstrap_models.load_config()
    assert cfg.retry_attempts == 5
    assert cfg.retry_base_delay == 2.5


def test_load_config_retry_defaults(monkeypatch):
    monkeypatch.delenv("GIGAAM_BOOTSTRAP_RETRIES", raising=False)
    monkeypatch.delenv("GIGAAM_BOOTSTRAP_RETRY_DELAY", raising=False)
    cfg = bootstrap_models.load_config()
    assert cfg.retry_attempts == 3
    assert cfg.retry_base_delay == 5.0
