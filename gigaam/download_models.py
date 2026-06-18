"""Download HuggingFace auxiliary models into HF_HOME cache."""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("gigaam-service.download")

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
_LOOPBACK_RE = re.compile(r"127\.0\.0\.1|localhost", re.IGNORECASE)


def _suspend_loopback_proxies() -> dict:
    """Remove proxy env vars that point to loopback addresses and return them for restoration.

    Docker Compose forwards host proxy vars (e.g. HTTP_PROXY=http://127.0.0.1:10808)
    into the container, but 127.0.0.1 inside the container is the container's own loopback —
    the host proxy is not listening there.  Keeping these vars causes download failures;
    removing them lets the connection go direct.  Non-loopback proxies are left intact.
    """
    suspended = {}
    for var in _PROXY_VARS:
        val = os.environ.get(var, "")
        if val and _LOOPBACK_RE.search(val):
            suspended[var] = os.environ.pop(var)
            logger.debug("Suspended loopback proxy %s=%s for download", var, val)
    return suspended


def _restore_proxies(saved: dict) -> None:
    for var, val in saved.items():
        os.environ[var] = val

PYANNOTE_MODEL = "pyannote/speaker-diarization-community-1"


def download_align_model(*, align_model: str, cache_dir: str) -> None:
    logger.info("Downloading alignment model: %s", align_model)
    from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

    Wav2Vec2Processor.from_pretrained(align_model, cache_dir=cache_dir)
    Wav2Vec2ForCTC.from_pretrained(align_model, cache_dir=cache_dir)
    logger.info("Alignment model cached.")


def download_pyannote(*, hf_token: str, cache_dir: str) -> bool:
    """Return True if cached, False if skipped (no token)."""
    if not hf_token:
        logger.warning(
            "HF_TOKEN missing — skipping Pyannote prefetch; diarize jobs need a cached pipeline or token"
        )
        return False
    logger.info("Downloading %s...", PYANNOTE_MODEL)
    from pyannote.audio import Pipeline

    Pipeline.from_pretrained(PYANNOTE_MODEL, token=hf_token)
    logger.info("Pyannote pipeline cached.")
    return True


def verify_silero() -> None:
    logger.info("Verifying Silero VAD (pip wheel)...")
    from silero_vad import load_silero_vad

    load_silero_vad()
    logger.info("Silero VAD OK.")


def download_deepfilter(*, model_name: str, model_dir: str) -> None:
    """Download DeepFilterNet model weights into model_dir on the volume.

    model_dir = <deepfilter_dir>/DeepFilterNet/<model_name>

    DeepFilterNet's init_df(default_model=name) downloads to
    user_cache_dir("DeepFilterNet")/<name> where user_cache_dir uses XDG_CACHE_HOME.
    We temporarily set XDG_CACHE_HOME = deepfilter_dir (= model_dir's grandparent) so that
    user_cache_dir("DeepFilterNet") resolves to model_dir's parent and the weights land at
    model_dir.  At runtime, init_df receives model_base_dir=model_dir to load offline.
    """
    # model_dir  = <deepfilter_dir>/DeepFilterNet/<model_name>
    # df_parent  = <deepfilter_dir>/DeepFilterNet  ← user_cache_dir("DeepFilterNet")
    # xdg_home   = <deepfilter_dir>               ← XDG_CACHE_HOME we need
    xdg_home = os.path.dirname(os.path.dirname(model_dir))
    os.makedirs(os.path.dirname(model_dir), exist_ok=True)

    logger.info("Downloading DeepFilterNet %s to %s (XDG_CACHE_HOME=%s)...", model_name, model_dir, xdg_home)

    old_xdg = os.environ.get("XDG_CACHE_HOME")
    os.environ["XDG_CACHE_HOME"] = xdg_home
    suspended = _suspend_loopback_proxies()
    try:
        from torchaudio_compat import apply_deepfilter_compat

        apply_deepfilter_compat()
        from df.enhance import init_df

        init_df(default_model=model_name)
    finally:
        _restore_proxies(suspended)
        if old_xdg is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = old_xdg

    logger.info("DeepFilterNet %s cached at %s.", model_name, model_dir)


def main() -> None:
    cache = os.environ.get("HF_HOME", "/app/data")
    align_model = os.environ.get(
        "GIGAAM_ALIGN_MODEL", "jonatasgrosman/wav2vec2-xls-r-1b-russian"
    )
    hf_token = os.environ.get("HF_TOKEN", "")
    denoise_model = os.environ.get("GIGAAM_DENOISE_MODEL", "DeepFilterNet3")
    deepfilter_dir = os.environ.get("GIGAAM_DEEPFILTER_DIR", os.path.join(cache, "deepfilter"))

    download_align_model(align_model=align_model, cache_dir=cache)
    download_pyannote(hf_token=hf_token, cache_dir=cache)
    verify_silero()
    download_deepfilter(
        model_name=denoise_model,
        model_dir=os.path.join(deepfilter_dir, denoise_model),
    )
    print("All GigaAM auxiliary models downloaded.")
