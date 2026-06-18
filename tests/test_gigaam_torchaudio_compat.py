"""DeepFilterNet import shim for torchaudio 2.9+."""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam")))

df = pytest.importorskip("df")
torchaudio = pytest.importorskip("torchaudio")


def test_deepfilter_imports_after_torchaudio_compat():
    from torchaudio_compat import apply_deepfilter_compat

    apply_deepfilter_compat()
    from df.enhance import enhance, init_df  # noqa: F401

    assert not hasattr(torchaudio, "backend") or hasattr(
        sys.modules.get("torchaudio.backend.common", object()), "AudioMetaData"
    )
