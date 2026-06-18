"""Bootstrap GigaAM model cache on volume (idempotent, offline-aware)."""
from __future__ import annotations

import glob
import json
import logging
import os
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import List, Optional

from bootstrap_state import STATE, BootstrapState
from download_models import (
    download_align_model,
    download_deepfilter,
    download_pyannote,
    verify_silero,
    PYANNOTE_MODEL,
)
from export_onnx import export_onnx, onnx_complete

logger = logging.getLogger("gigaam-service.bootstrap")

MANIFEST_SCHEMA = 1
MANIFEST_DIR = ".gigaam"
MANIFEST_FILE = "manifest.json"
LOCK_FILE = "bootstrap.lock"

_BOOTSTRAP_THREAD_LOCK = threading.Lock()


class CacheIncompleteError(RuntimeError):
    pass


class CacheInitError(RuntimeError):
    pass


@dataclass(frozen=True)
class BootstrapConfig:
    cache_dir: str
    weights_dir: str
    onnx_dir: str
    deepfilter_dir: str
    model: str
    align_model: str
    denoise_enabled: bool
    prefetch_diarization: bool
    offline_mode: bool
    prune_pytorch: bool
    hf_token: str
    denoise_model: str = "DeepFilterNet3"
    retry_attempts: int = 3
    retry_base_delay: float = 5.0


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _retry(fn, *, attempts: int, base_delay: float, label: str = "step"):
    """Call ``fn`` with retries on transient failures, exponential backoff."""
    attempts = max(1, attempts)
    last_exc: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "Bootstrap step '%s' failed (attempt %d/%d): %s; retrying in %.1fs",
                label,
                attempt,
                attempts,
                exc,
                delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def load_config() -> BootstrapConfig:
    return BootstrapConfig(
        cache_dir=os.environ.get("HF_HOME", "/app/data"),
        weights_dir=os.environ.get("GIGAAM_WEIGHTS_DIR", "/app/data/gigaam"),
        onnx_dir=os.environ.get("GIGAAM_ONNX_DIR", "/app/data/gigaam_onnx"),
        deepfilter_dir=os.environ.get("GIGAAM_DEEPFILTER_DIR", "/app/data/deepfilter"),
        model=os.environ.get("GIGAAM_MODEL", "v3_e2e_rnnt"),
        align_model=os.environ.get(
            "GIGAAM_ALIGN_MODEL", "jonatasgrosman/wav2vec2-xls-r-1b-russian"
        ),
        denoise_enabled=_env_bool("GIGAAM_DENOISE", True),
        prefetch_diarization=_env_bool("GIGAAM_PREFETCH_DIARIZATION", True),
        offline_mode=_env_bool("GIGAAM_OFFLINE_MODE", False),
        prune_pytorch=_env_bool("GIGAAM_PRUNE_PYTORCH_AFTER_ONNX", False),
        hf_token=os.environ.get("HF_TOKEN", ""),
        denoise_model=os.environ.get("GIGAAM_DENOISE_MODEL", "DeepFilterNet3"),
        retry_attempts=_env_int("GIGAAM_BOOTSTRAP_RETRIES", 3),
        retry_base_delay=_env_float("GIGAAM_BOOTSTRAP_RETRY_DELAY", 5.0),
    )


def manifest_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, MANIFEST_DIR, MANIFEST_FILE)


def init_cache_dirs(base: str | None = None) -> None:
    base = base or os.environ.get("HF_HOME", "/app/data")
    deepfilter_dir = os.environ.get("GIGAAM_DEEPFILTER_DIR", os.path.join(base, "deepfilter"))
    try:
        for sub in (MANIFEST_DIR, "gigaam", "gigaam_onnx", "hub", "torch"):
            os.makedirs(os.path.join(base, sub), exist_ok=True)
        os.makedirs(deepfilter_dir, exist_ok=True)
    except OSError as exc:
        raise CacheInitError(
            f"Не удалось инициализировать кэш в {base}: {exc}"
        ) from exc


def enable_offline_mode() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"


def enable_online_mode() -> None:
    os.environ["HF_HUB_OFFLINE"] = "0"
    os.environ.pop("TRANSFORMERS_OFFLINE", None)


def _hf_cached(repo_id: str, cache_dir: str, filenames: tuple[str, ...]) -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache

        for name in filenames:
            if try_to_load_from_cache(repo_id, name, cache_dir=cache_dir) is not None:
                return True
    except Exception as exc:
        logger.debug("HF cache check failed for %s: %s", repo_id, exc)
    # Fallback: hub directory with snapshots
    slug = "models--" + repo_id.replace("/", "--")
    root = os.path.join(cache_dir, "hub", slug, "snapshots")
    if not os.path.isdir(root):
        return False
    for snap in glob.glob(os.path.join(root, "*")):
        if not os.path.isdir(snap):
            continue
        for name in filenames:
            if os.path.isfile(os.path.join(snap, name)):
                return True
    return False


def align_cached(cfg: BootstrapConfig) -> bool:
    return _hf_cached(
        cfg.align_model,
        cfg.cache_dir,
        ("config.json", "pytorch_model.bin", "model.safetensors"),
    )


def pyannote_cached(cfg: BootstrapConfig) -> bool:
    return _hf_cached(
        PYANNOTE_MODEL,
        cfg.cache_dir,
        ("config.yaml", "pytorch_model.bin", "model.safetensors"),
    )


def deepfilter_model_dir(cfg: BootstrapConfig) -> str:
    """Return the directory where DeepFilterNet model files are stored on the volume.

    DeepFilterNet resolves its model directory as user_cache_dir("DeepFilterNet")/<model>.
    We set XDG_CACHE_HOME=cfg.deepfilter_dir during download so:
      user_cache_dir("DeepFilterNet") = cfg.deepfilter_dir/DeepFilterNet
      model dir                       = cfg.deepfilter_dir/DeepFilterNet/<model>
    """
    return os.path.join(cfg.deepfilter_dir, "DeepFilterNet", cfg.denoise_model)


def deepfilter_cached(cfg: BootstrapConfig) -> bool:
    """True iff DeepFilterNet3 weights are present on the volume."""
    model_dir = deepfilter_model_dir(cfg)
    if not os.path.isdir(model_dir):
        return False
    return os.path.isfile(os.path.join(model_dir, "config.ini"))


def baked_deepfilter_dir() -> str:
    """Return the path where DeepFilterNet weights are baked into the image at build time."""
    return os.environ.get("GIGAAM_BAKED_DEEPFILTER_DIR", "/opt/deepfilter")


def baked_deepfilter_model_dir(cfg: BootstrapConfig) -> str:
    """Return the baked-image model dir mirroring the volume layout."""
    return os.path.join(baked_deepfilter_dir(), "DeepFilterNet", cfg.denoise_model)


def baked_deepfilter_available(cfg: BootstrapConfig) -> bool:
    """True iff DeepFilterNet weights were baked into the image and are readable."""
    return os.path.isfile(os.path.join(baked_deepfilter_model_dir(cfg), "config.ini"))


def seed_deepfilter_from_baked(cfg: BootstrapConfig) -> bool:
    """Copy baked weights from the image path to the volume. Returns True on success."""
    src = baked_deepfilter_model_dir(cfg)
    if not os.path.isfile(os.path.join(src, "config.ini")):
        return False
    dst = deepfilter_model_dir(cfg)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.isdir(dst):
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    logger.info("Seeded DeepFilterNet %s from baked image cache (offline)", cfg.denoise_model)
    return True


def diarization_available(cfg: BootstrapConfig) -> bool:
    """True iff runtime diarization can succeed: HF_TOKEN set AND Pyannote cached."""
    return bool(cfg.hf_token) and pyannote_cached(cfg)


def pytorch_weights_present(cfg: BootstrapConfig) -> bool:
    root = cfg.weights_dir
    if not os.path.isdir(root):
        return False
    return any(os.path.isfile(os.path.join(root, f)) for f in os.listdir(root))


def read_manifest(cfg: BootstrapConfig) -> Optional[dict]:
    path = manifest_path(cfg.cache_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Invalid manifest %s: %s", path, exc)
        return None


def manifest_matches_env(manifest: dict, cfg: BootstrapConfig) -> bool:
    env = manifest.get("env") or {}
    if env.get("GIGAAM_MODEL") != cfg.model:
        return False
    if env.get("GIGAAM_ALIGN_MODEL") != cfg.align_model:
        return False
    if bool(env.get("GIGAAM_PREFETCH_DIARIZATION")) != cfg.prefetch_diarization:
        return False
    if cfg.denoise_enabled and env.get("GIGAAM_DENOISE_MODEL") != cfg.denoise_model:
        return False
    if cfg.prefetch_diarization and not bool(manifest.get("pyannote_cached")):
        if cfg.hf_token and not pyannote_cached(cfg):
            return False
    return True


def verify_cache_artifacts(cfg: BootstrapConfig) -> List[str]:
    missing: List[str] = []
    if not onnx_complete(cfg.onnx_dir, cfg.model):
        missing.append(f"onnx:{cfg.onnx_dir}")
    if not align_cached(cfg):
        missing.append(f"align:{cfg.align_model}")
    if cfg.denoise_enabled and not deepfilter_cached(cfg):
        missing.append(f"deepfilter:{cfg.denoise_model}")
    if cfg.prefetch_diarization and cfg.hf_token and not pyannote_cached(cfg):
        missing.append(f"pyannote:{PYANNOTE_MODEL}")
    return missing


def cached_model_summary(cfg: BootstrapConfig) -> List[str]:
    items = ["onnx", "align", "silero"]
    if cfg.denoise_enabled and deepfilter_cached(cfg):
        items.append("deepfilter")
    if pyannote_cached(cfg):
        items.append("pyannote")
    return items


def models_ready(cfg: BootstrapConfig | None = None) -> bool:
    cfg = cfg or load_config()
    manifest = read_manifest(cfg)
    if manifest is None:
        return False
    if not manifest_matches_env(manifest, cfg):
        return False
    return len(verify_cache_artifacts(cfg)) == 0


def write_manifest(cfg: BootstrapConfig, *, pyannote_ok: bool) -> None:
    path = manifest_path(cfg.cache_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "schema_version": MANIFEST_SCHEMA,
        "env": {
            "GIGAAM_MODEL": cfg.model,
            "GIGAAM_ALIGN_MODEL": cfg.align_model,
            "GIGAAM_DENOISE": cfg.denoise_enabled,
            "GIGAAM_DENOISE_MODEL": cfg.denoise_model,
            "GIGAAM_PREFETCH_DIARIZATION": cfg.prefetch_diarization,
        },
        "pyannote_cached": pyannote_ok,
        "components": {
            "onnx": onnx_complete(cfg.onnx_dir, cfg.model),
            "align": align_cached(cfg),
            "deepfilter": deepfilter_cached(cfg) if cfg.denoise_enabled else False,
            "pyannote": pyannote_ok,
        },
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


@contextmanager
def bootstrap_file_lock(cache_dir: str):
    lock_path = os.path.join(cache_dir, MANIFEST_DIR, LOCK_FILE)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    try:
        import fcntl

        with open(lock_path, "w", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except ImportError:
        with _BOOTSTRAP_THREAD_LOCK:
            yield


STEP_LABELS = {
    "init_cache": "инициализация volume",
    "download_pytorch": "GigaAM PyTorch",
    "export_onnx": "экспорт ONNX",
    "download_align": "Wav2Vec2 alignment",
    "download_deepfilter": "DeepFilterNet3 weights",
    "download_pyannote": "Pyannote diarization",
    "verify_wheels": "Silero VAD",
}


def _planned_steps(cfg: BootstrapConfig) -> List[str]:
    steps: List[str] = []
    if not onnx_complete(cfg.onnx_dir, cfg.model):
        if not pytorch_weights_present(cfg):
            steps.append("download_pytorch")
        steps.append("export_onnx")
    if not align_cached(cfg):
        steps.append("download_align")
    if cfg.denoise_enabled and not deepfilter_cached(cfg):
        steps.append("download_deepfilter")
    if cfg.prefetch_diarization and cfg.hf_token and not pyannote_cached(cfg):
        steps.append("download_pyannote")
    steps.append("verify_wheels")
    return steps


def _step_message(cfg: BootstrapConfig, step: str, index: int, total: int) -> str:
    label = STEP_LABELS.get(step, step)
    prefix = "Первый запуск: кэш моделей пуст, " if not read_manifest(cfg) else ""
    return (
        f"{prefix}Контейнер не готов к работе: скачиваются модели в кэш "
        f"(шаг {index}/{total} — {label})"
    )


def _download_pytorch(cfg: BootstrapConfig) -> None:
    import gigaam

    logger.info("Downloading GigaAM PyTorch weights %s...", cfg.model)
    model = gigaam.load_model(
        cfg.model,
        fp16_encoder=False,
        use_flash=False,
        device="cpu",
        download_root=cfg.weights_dir,
    )
    del model


def _prune_pytorch(cfg: BootstrapConfig) -> None:
    if cfg.prune_pytorch and os.path.isdir(cfg.weights_dir):
        logger.info("Pruning PyTorch weights at %s", cfg.weights_dir)
        shutil.rmtree(cfg.weights_dir, ignore_errors=True)


def _healthy_message(cfg: BootstrapConfig, diar_available: bool) -> str:
    base = "Все модели в кэше, сервис готов к транскрипции"
    if cfg.prefetch_diarization and not diar_available:
        return base + " (диаризация отключена: не задан HF_TOKEN или Pyannote не закэширован)"
    return base


def _warn_if_diarization_unconfigured(cfg: BootstrapConfig, diar_available: bool) -> None:
    if cfg.prefetch_diarization and not diar_available:
        logger.warning(
            "GIGAAM_PREFETCH_DIARIZATION=true but diarization is unavailable "
            "(HF_TOKEN set=%s, pyannote_cached=%s); diarize=true will be rejected (HTTP 400).",
            bool(cfg.hf_token),
            pyannote_cached(cfg),
        )


def ensure_models(state: BootstrapState | None = None) -> None:
    state = state or STATE
    cfg = load_config()

    init_cache_dirs(cfg.cache_dir)
    first_install = read_manifest(cfg) is None
    state.update(
        status="bootstrapping",
        ready=False,
        first_install=first_install,
        phase="init_cache",
        message="Первый запуск: инициализация кэша..."
        if first_install
        else "Проверка кэша моделей...",
    )

    if models_ready(cfg):
        enable_offline_mode()
        py_ok = pyannote_cached(cfg)
        diar_av = diarization_available(cfg)
        _warn_if_diarization_unconfigured(cfg, diar_av)
        state.update(
            status="healthy",
            ready=True,
            first_install=False,
            phase="ready",
            message=_healthy_message(cfg, diar_av),
            cached_models=cached_model_summary(cfg),
            pyannote_cached=py_ok,
            diarization_available=diar_av,
            missing_artifacts=[],
        )
        logger.info("Model cache ready (offline mode)")
        return

    if cfg.offline_mode:
        missing = verify_cache_artifacts(cfg)
        msg = (
            "Кэш неполный, автозагрузка отключена (GIGAAM_OFFLINE_MODE). "
            "Выполните seed кэша вручную — см. INSTALL.md §3.2."
        )
        if first_install:
            msg = (
                "Первый запуск: volume пуст, импортируйте кэш (INSTALL.md) "
                "или отключите GIGAAM_OFFLINE_MODE."
            )
        state.update(
            status="failed",
            ready=False,
            phase="failed",
            message=msg,
            missing_artifacts=missing,
            error=msg,
        )
        raise CacheIncompleteError(msg)

    steps = _planned_steps(cfg)
    total = len(steps)
    pyannote_ok = pyannote_cached(cfg)

    with bootstrap_file_lock(cfg.cache_dir):
        enable_online_mode()
        try:
            for idx, step in enumerate(steps, start=1):
                state.update(
                    status="bootstrapping",
                    ready=False,
                    phase=step,
                    step=idx,
                    steps_total=total,
                    message=_step_message(cfg, step, idx, total),
                    first_install=first_install,
                )
                logger.info(state.message)

                if step == "download_pytorch":
                    _retry(
                        lambda: _download_pytorch(cfg),
                        attempts=cfg.retry_attempts,
                        base_delay=cfg.retry_base_delay,
                        label=step,
                    )
                elif step == "export_onnx":
                    export_onnx(
                        weights_dir=cfg.weights_dir,
                        onnx_dir=cfg.onnx_dir,
                        model=cfg.model,
                    )
                    _prune_pytorch(cfg)
                elif step == "download_align":
                    _retry(
                        lambda: download_align_model(
                            align_model=cfg.align_model, cache_dir=cfg.cache_dir
                        ),
                        attempts=cfg.retry_attempts,
                        base_delay=cfg.retry_base_delay,
                        label=step,
                    )
                elif step == "download_deepfilter":
                    if seed_deepfilter_from_baked(cfg):
                        pass  # offline seed from image — no network needed
                    else:
                        try:
                            _retry(
                                lambda: download_deepfilter(
                                    model_name=cfg.denoise_model,
                                    model_dir=deepfilter_model_dir(cfg),
                                ),
                                attempts=cfg.retry_attempts,
                                base_delay=cfg.retry_base_delay,
                                label=step,
                            )
                        except Exception as exc:
                            logger.warning(
                                "DeepFilterNet download failed, denoising disabled for this session: %s",
                                exc,
                            )
                            cfg = replace(cfg, denoise_enabled=False)
                            state.update(
                                status="bootstrapping",
                                message="DeepFilterNet недоступен — шумоподавление отключено, продолжаем без него",
                            )
                elif step == "download_pyannote":
                    pyannote_ok = _retry(
                        lambda: download_pyannote(
                            hf_token=cfg.hf_token, cache_dir=cfg.cache_dir
                        ),
                        attempts=cfg.retry_attempts,
                        base_delay=cfg.retry_base_delay,
                        label=step,
                    )
                elif step == "verify_wheels":
                    verify_silero()

            if cfg.prefetch_diarization and not pyannote_ok:
                pyannote_ok = pyannote_cached(cfg)

            missing = verify_cache_artifacts(cfg)
            if missing:
                raise CacheIncompleteError(
                    f"Кэш неполный после bootstrap: {', '.join(missing)}"
                )

            write_manifest(cfg, pyannote_ok=pyannote_ok)
            enable_offline_mode()
            diar_av = diarization_available(cfg)
            _warn_if_diarization_unconfigured(cfg, diar_av)
            state.update(
                status="healthy",
                ready=True,
                first_install=False,
                phase="ready",
                step=total,
                steps_total=total,
                message=_healthy_message(cfg, diar_av),
                cached_models=cached_model_summary(cfg),
                pyannote_cached=pyannote_ok,
                diarization_available=diar_av,
                missing_artifacts=[],
                error=None,
            )
            logger.info("Bootstrap complete")
        except Exception as exc:
            msg = str(exc)
            if "huggingface" in msg.lower() or "connection" in msg.lower():
                msg = (
                    f"Первый запуск: не удалось скачать модели ({exc}). "
                    "Требуется интернет, прокси (HTTP_PROXY) или seed кэша."
                )
            state.update(
                status="failed",
                ready=False,
                phase="failed",
                message=msg,
                error=msg,
                missing_artifacts=verify_cache_artifacts(cfg),
            )
            raise


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        ensure_models()
    except CacheIncompleteError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
