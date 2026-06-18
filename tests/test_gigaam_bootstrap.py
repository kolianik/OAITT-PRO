"""Tests for GigaAM volume bootstrap and health endpoints."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

GIGAAM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam"))
sys.path.insert(0, GIGAAM_ROOT)

from bootstrap_models import (  # noqa: E402
    BootstrapConfig,
    CacheIncompleteError,
    init_cache_dirs,
    manifest_path,
    models_ready,
    ensure_models,
    write_manifest,
    deepfilter_cached,
    deepfilter_model_dir,
    cached_model_summary,
    verify_cache_artifacts,
    _planned_steps,
    baked_deepfilter_available,
    baked_deepfilter_model_dir,
    seed_deepfilter_from_baked,
)
from bootstrap_state import BootstrapState
from export_onnx import onnx_artifact_names


@pytest.fixture
def cache_tmp(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        cfg = BootstrapConfig(
            cache_dir=tmp,
            weights_dir=os.path.join(tmp, "gigaam"),
            onnx_dir=os.path.join(tmp, "gigaam_onnx"),
            deepfilter_dir=os.path.join(tmp, "deepfilter"),
            model="v3_e2e_rnnt",
            align_model="jonatasgrosman/wav2vec2-xls-r-1b-russian",
            denoise_enabled=True,
            prefetch_diarization=False,
            offline_mode=False,
            prune_pytorch=False,
            hf_token="",
        )
        monkeypatch.setenv("HF_HOME", tmp)
        monkeypatch.setenv("GIGAAM_WEIGHTS_DIR", cfg.weights_dir)
        monkeypatch.setenv("GIGAAM_ONNX_DIR", cfg.onnx_dir)
        monkeypatch.setenv("GIGAAM_DEEPFILTER_DIR", cfg.deepfilter_dir)
        monkeypatch.setenv("GIGAAM_MODEL", cfg.model)
        monkeypatch.setenv("GIGAAM_PREFETCH_DIARIZATION", "false")
        monkeypatch.setenv("GIGAAM_OFFLINE_MODE", "false")
        yield cfg


def test_init_cache_dirs_on_empty_volume(cache_tmp):
    init_cache_dirs(cache_tmp.cache_dir)
    for sub in (".gigaam", "gigaam", "gigaam_onnx", "hub"):
        assert os.path.isdir(os.path.join(cache_tmp.cache_dir, sub))


def test_onnx_artifact_names():
    names = onnx_artifact_names("v3_e2e_rnnt")
    assert "v3_e2e_rnnt_encoder.onnx" in names
    assert "v3_e2e_rnnt.yaml" in names


def test_models_ready_false_without_manifest(cache_tmp):
    assert models_ready(cache_tmp) is False


def test_models_ready_true_with_onnx_and_manifest(cache_tmp):
    os.makedirs(cache_tmp.onnx_dir, exist_ok=True)
    for name in onnx_artifact_names(cache_tmp.model):
        Path(cache_tmp.onnx_dir, name).write_text("x", encoding="utf-8")

    hub_root = os.path.join(
        cache_tmp.cache_dir,
        "hub",
        "models--jonatasgrosman--wav2vec2-xls-r-1b-russian",
        "snapshots",
        "abc",
    )
    os.makedirs(hub_root, exist_ok=True)
    Path(hub_root, "config.json").write_text("{}", encoding="utf-8")
    Path(hub_root, "model.safetensors").write_text("x", encoding="utf-8")

    # denoise_enabled=True in fixture: deepfilter weights are also required.
    df_dir = deepfilter_model_dir(cache_tmp)
    os.makedirs(df_dir, exist_ok=True)
    Path(df_dir, "config.ini").write_text("[deepfilter]", encoding="utf-8")

    write_manifest(cache_tmp, pyannote_ok=False)
    assert models_ready(cache_tmp) is True


def test_offline_mode_empty_volume_fails(cache_tmp, monkeypatch):
    state = BootstrapState()
    monkeypatch.setenv("GIGAAM_OFFLINE_MODE", "true")
    cfg = BootstrapConfig(
        cache_dir=cache_tmp.cache_dir,
        weights_dir=cache_tmp.weights_dir,
        onnx_dir=cache_tmp.onnx_dir,
        deepfilter_dir=cache_tmp.deepfilter_dir,
        model=cache_tmp.model,
        align_model=cache_tmp.align_model,
        denoise_enabled=True,
        prefetch_diarization=False,
        offline_mode=True,
        prune_pytorch=False,
        hf_token="",
    )
    with pytest.raises(CacheIncompleteError, match="volume пуст|Кэш неполный"):
        ensure_models(state)
    assert state.status == "failed"
    assert state.ready is False
    _ = cfg


def test_ensure_models_skips_network_when_ready(cache_tmp, monkeypatch):
    os.makedirs(cache_tmp.onnx_dir, exist_ok=True)
    for name in onnx_artifact_names(cache_tmp.model):
        Path(cache_tmp.onnx_dir, name).write_text("x", encoding="utf-8")
    hub_root = os.path.join(
        cache_tmp.cache_dir,
        "hub",
        "models--jonatasgrosman--wav2vec2-xls-r-1b-russian",
        "snapshots",
        "abc",
    )
    os.makedirs(hub_root, exist_ok=True)
    Path(hub_root, "config.json").write_text("{}", encoding="utf-8")
    Path(hub_root, "model.safetensors").write_text("x", encoding="utf-8")
    df_dir = deepfilter_model_dir(cache_tmp)
    os.makedirs(df_dir, exist_ok=True)
    Path(df_dir, "config.ini").write_text("[deepfilter]", encoding="utf-8")
    write_manifest(cache_tmp, pyannote_ok=False)

    state = BootstrapState()
    with mock.patch("bootstrap_models.download_align_model") as dl:
        ensure_models(state)
        dl.assert_not_called()
    assert state.ready is True
    assert os.environ.get("HF_HUB_OFFLINE") == "1"


def test_health_endpoint_bootstrapping():
    from fastapi.testclient import TestClient

    import main as gigaam_main
    from bootstrap_state import STATE as LIVE_STATE

    prev = LIVE_STATE.snapshot()
    try:
        LIVE_STATE.update(
            status="bootstrapping",
            ready=False,
            first_install=True,
            message="Первый запуск: тест",
            phase="init_cache",
            step=1,
            steps_total=5,
        )
        client = TestClient(gigaam_main.app)
        resp = client.get("/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["ready"] is False
        assert data["first_install"] is True
        assert "Первый запуск" in data["message"]
    finally:
        LIVE_STATE.update(**prev)


def test_health_endpoint_healthy():
    from fastapi.testclient import TestClient

    import main as gigaam_main
    from bootstrap_state import STATE as LIVE_STATE

    prev = LIVE_STATE.snapshot()
    try:
        LIVE_STATE.update(
            status="healthy",
            ready=True,
            message="Все модели в кэше, сервис готов к транскрипции",
            cached_models=["onnx", "align"],
        )
        client = TestClient(gigaam_main.app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["ready"] is True
    finally:
        LIVE_STATE.update(**prev)


def test_healthcheck_script_prints_message_on_failure():
    import healthcheck

    payload = json.dumps(
        {"status": "bootstrapping", "ready": False, "message": "Скачивание моделей"}
    ).encode()

    class FakeResp:
        status = 503

        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
        rc = healthcheck.main()
    assert rc == 1


def test_manifest_path():
    path = manifest_path("/app/data").replace("\\", "/")
    assert path.endswith(".gigaam/manifest.json")


# --- BUG-002: DeepFilterNet3 volume persistence ---

def _make_full_cache(cache_tmp):
    """Populate ONNX + align artifacts so only deepfilter state is the variable."""
    os.makedirs(cache_tmp.onnx_dir, exist_ok=True)
    for name in onnx_artifact_names(cache_tmp.model):
        Path(cache_tmp.onnx_dir, name).write_text("x", encoding="utf-8")
    hub_root = os.path.join(
        cache_tmp.cache_dir,
        "hub",
        "models--jonatasgrosman--wav2vec2-xls-r-1b-russian",
        "snapshots",
        "abc",
    )
    os.makedirs(hub_root, exist_ok=True)
    Path(hub_root, "config.json").write_text("{}", encoding="utf-8")
    Path(hub_root, "model.safetensors").write_text("x", encoding="utf-8")


def _write_deepfilter_marker(cfg):
    """Simulate downloaded DeepFilterNet3 weights."""
    model_dir = deepfilter_model_dir(cfg)
    os.makedirs(model_dir, exist_ok=True)
    Path(model_dir, "config.ini").write_text("[deepfilter]", encoding="utf-8")


def test_deepfilter_cached_false_on_empty_dir(cache_tmp):
    assert deepfilter_cached(cache_tmp) is False


def test_deepfilter_cached_true_when_marker_present(cache_tmp):
    _write_deepfilter_marker(cache_tmp)
    assert deepfilter_cached(cache_tmp) is True


def test_deepfilter_model_dir_under_deepfilter_dir(cache_tmp):
    d = deepfilter_model_dir(cache_tmp)
    assert d.startswith(cache_tmp.deepfilter_dir)


def test_verify_cache_artifacts_includes_deepfilter_when_missing(cache_tmp):
    _make_full_cache(cache_tmp)
    missing = verify_cache_artifacts(cache_tmp)
    assert any("deepfilter" in m for m in missing)


def test_verify_cache_artifacts_no_deepfilter_when_present(cache_tmp):
    _make_full_cache(cache_tmp)
    _write_deepfilter_marker(cache_tmp)
    missing = verify_cache_artifacts(cache_tmp)
    assert not any("deepfilter" in m for m in missing)


def test_verify_cache_artifacts_no_deepfilter_when_denoise_disabled(cache_tmp):
    import dataclasses
    cfg = dataclasses.replace(cache_tmp, denoise_enabled=False)
    _make_full_cache(cache_tmp)
    missing = verify_cache_artifacts(cfg)
    assert not any("deepfilter" in m for m in missing)


def test_models_ready_false_when_deepfilter_weights_missing(cache_tmp):
    """Regression: manifest + ONNX + align present but no deepfilter → not ready."""
    _make_full_cache(cache_tmp)
    write_manifest(cache_tmp, pyannote_ok=False)
    assert models_ready(cache_tmp) is False


def test_models_ready_true_when_deepfilter_weights_present(cache_tmp):
    _make_full_cache(cache_tmp)
    _write_deepfilter_marker(cache_tmp)
    write_manifest(cache_tmp, pyannote_ok=False)
    assert models_ready(cache_tmp) is True


def test_cached_model_summary_includes_deepfilter_only_when_cached(cache_tmp):
    summary_without = cached_model_summary(cache_tmp)
    assert "deepfilter" not in summary_without

    _write_deepfilter_marker(cache_tmp)
    summary_with = cached_model_summary(cache_tmp)
    assert "deepfilter" in summary_with


def test_planned_steps_includes_download_deepfilter_when_weights_missing(cache_tmp):
    steps = _planned_steps(cache_tmp)
    assert "download_deepfilter" in steps


def test_planned_steps_excludes_download_deepfilter_when_weights_present(cache_tmp):
    _write_deepfilter_marker(cache_tmp)
    steps = _planned_steps(cache_tmp)
    assert "download_deepfilter" not in steps


def test_planned_steps_excludes_download_deepfilter_when_denoise_disabled(cache_tmp):
    import dataclasses
    cfg = dataclasses.replace(cache_tmp, denoise_enabled=False)
    steps = _planned_steps(cfg)
    assert "download_deepfilter" not in steps


def test_offline_mode_fails_when_deepfilter_missing(cache_tmp, monkeypatch):
    """GIGAAM_OFFLINE_MODE + denoise enabled + missing weights → CacheIncompleteError listing deepfilter."""
    _make_full_cache(cache_tmp)
    monkeypatch.setenv("GIGAAM_OFFLINE_MODE", "true")
    import dataclasses
    cfg = dataclasses.replace(cache_tmp, offline_mode=True)
    state = BootstrapState()
    with pytest.raises(CacheIncompleteError) as exc_info:
        ensure_models(state)
    assert state.status == "failed"
    assert any("deepfilter" in a for a in state.missing_artifacts)
    _ = cfg


def test_manifest_tracks_denoise_model(cache_tmp):
    """Manifest records GIGAAM_DENOISE_MODEL; changing model invalidates manifest."""
    _make_full_cache(cache_tmp)
    _write_deepfilter_marker(cache_tmp)
    write_manifest(cache_tmp, pyannote_ok=False)
    assert models_ready(cache_tmp) is True

    import dataclasses
    cfg_other = dataclasses.replace(cache_tmp, denoise_model="DeepFilterNet2")
    assert models_ready(cfg_other) is False


# --- BUG-003: DeepFilterNet3 baked into image, offline seed at bootstrap ---

@pytest.fixture
def baked_tmp(cache_tmp, tmp_path, monkeypatch):
    """Extend cache_tmp with a fake baked-image DeepFilterNet dir."""
    baked_dir = str(tmp_path / "baked_deepfilter")
    monkeypatch.setenv("GIGAAM_BAKED_DEEPFILTER_DIR", baked_dir)
    return cache_tmp, baked_dir


def _write_baked_deepfilter(baked_dir: str, model_name: str = "DeepFilterNet3") -> str:
    """Simulate a baked DeepFilterNet model dir (as created by docker build)."""
    import dataclasses as dc
    cfg_fake = BootstrapConfig(
        cache_dir="/tmp", weights_dir="/tmp", onnx_dir="/tmp",
        deepfilter_dir=baked_dir, model="v3_e2e_rnnt",
        align_model="x", denoise_enabled=True, prefetch_diarization=False,
        offline_mode=False, prune_pytorch=False, hf_token="",
        denoise_model=model_name,
    )
    model_dir = os.path.join(baked_dir, "DeepFilterNet", model_name)
    os.makedirs(model_dir, exist_ok=True)
    Path(model_dir, "config.ini").write_text("[deepfilter]", encoding="utf-8")
    Path(model_dir, "model.onnx").write_bytes(b"\x00" * 8)
    return model_dir


def test_baked_deepfilter_available_false_when_empty(baked_tmp):
    cfg, baked_dir = baked_tmp
    assert baked_deepfilter_available(cfg) is False


def test_baked_deepfilter_available_true_when_marker_present(baked_tmp):
    cfg, baked_dir = baked_tmp
    _write_baked_deepfilter(baked_dir, cfg.denoise_model)
    assert baked_deepfilter_available(cfg) is True


def test_baked_deepfilter_model_dir_uses_env(baked_tmp):
    cfg, baked_dir = baked_tmp
    d = baked_deepfilter_model_dir(cfg)
    assert d.startswith(baked_dir)
    assert cfg.denoise_model in d


def test_seed_deepfilter_from_baked_copies_to_volume(baked_tmp):
    cfg, baked_dir = baked_tmp
    _write_baked_deepfilter(baked_dir, cfg.denoise_model)
    result = seed_deepfilter_from_baked(cfg)
    assert result is True
    assert deepfilter_cached(cfg) is True


def test_seed_deepfilter_from_baked_returns_false_when_no_baked(baked_tmp):
    cfg, baked_dir = baked_tmp
    result = seed_deepfilter_from_baked(cfg)
    assert result is False
    assert deepfilter_cached(cfg) is False


def test_download_deepfilter_step_prefers_baked_no_network(baked_tmp, monkeypatch):
    """ensure_models() uses offline seed when baked weights are present; network not called."""
    cfg, baked_dir = baked_tmp
    _make_full_cache(cfg)
    _write_baked_deepfilter(baked_dir, cfg.denoise_model)

    state = BootstrapState()
    with mock.patch("bootstrap_models.download_deepfilter") as net_dl, \
         mock.patch("bootstrap_models.verify_silero"):
        ensure_models(state)
        net_dl.assert_not_called()

    assert state.ready is True
    assert "deepfilter" in state.cached_models


def test_download_deepfilter_step_falls_back_to_network_when_no_baked(baked_tmp, monkeypatch):
    """When baked dir is absent, network download_deepfilter is called."""
    cfg, baked_dir = baked_tmp
    _make_full_cache(cfg)

    state = BootstrapState()

    def fake_download(*, model_name, model_dir):
        os.makedirs(model_dir, exist_ok=True)
        Path(model_dir, "config.ini").write_text("[deepfilter]", encoding="utf-8")

    with mock.patch("bootstrap_models.download_deepfilter", side_effect=fake_download) as net_dl, \
         mock.patch("bootstrap_models.verify_silero"):
        ensure_models(state)
        net_dl.assert_called_once()

    assert state.ready is True
    assert "deepfilter" in state.cached_models
