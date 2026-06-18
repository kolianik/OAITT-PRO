"""DeepFilterNet 0.5.6 compatibility with torchaudio 2.9+ (backend module removed)."""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from typing import Any

_APPLIED = False
_GIT_PATCHED = False


@dataclass
class AudioMetaData:
    sample_rate: int = 48000
    num_frames: int = 0
    num_channels: int = 1
    bits_per_sample: int = 16
    encoding: str = "PCM_S"


def apply_deepfilter_torchaudio_compat() -> None:
    """Register torchaudio.backend stubs required by deepfilternet's df/io.py."""
    global _APPLIED
    if _APPLIED:
        return

    import torchaudio as ta

    backend = types.ModuleType("torchaudio.backend")
    common = types.ModuleType("torchaudio.backend.common")
    common.AudioMetaData = AudioMetaData
    backend.common = common
    sys.modules["torchaudio.backend"] = backend
    sys.modules["torchaudio.backend.common"] = common

    if not hasattr(ta, "AudioMetaData"):
        ta.AudioMetaData = AudioMetaData  # type: ignore[attr-defined]

    if not hasattr(ta, "info"):
        import soundfile as sf

        def _info(path: str, **_: Any) -> AudioMetaData:
            inf = sf.info(path)
            return AudioMetaData(
                sample_rate=int(inf.samplerate),
                num_frames=int(inf.frames),
                num_channels=int(inf.channels),
                bits_per_sample=16,
                encoding=str(inf.subtype),
            )

        ta.info = _info  # type: ignore[attr-defined]

    _APPLIED = True


def apply_deepfilter_git_compat() -> None:
    """Skip git subprocess in deepfilternet logger (git not in slim runtime images)."""
    global _GIT_PATCHED
    if _GIT_PATCHED:
        return

    import df.utils as df_utils

    def _no_git() -> None:
        return None

    df_utils.get_git_root = _no_git  # type: ignore[assignment]
    df_utils.get_commit_hash = _no_git  # type: ignore[assignment]
    df_utils.get_branch_name = _no_git  # type: ignore[assignment]
    _GIT_PATCHED = True


def apply_deepfilter_compat() -> None:
    """Apply all DeepFilterNet runtime shims (torchaudio 2.9+, no git in image)."""
    apply_deepfilter_torchaudio_compat()
    apply_deepfilter_git_compat()
