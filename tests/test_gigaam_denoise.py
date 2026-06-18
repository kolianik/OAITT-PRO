"""Tests for denoise split-path routing and passthrough."""
import os
import sys
import tempfile

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam")))

from pipeline.denoise import passthrough_clean


def test_passthrough_preserves_duration(tmp_path):
    sr = 16000
    audio = np.random.randn(sr * 3).astype(np.float32) * 0.01
    src = tmp_path / "orig.wav"
    dst = tmp_path / "clean.wav"
    sf.write(str(src), audio, sr)
    passthrough_clean(str(src), str(dst))
    out, out_sr = sf.read(str(dst))
    assert out_sr == sr
    assert abs(len(out) / sr - 3.0) < 0.01


def test_split_path_routing_constants():
    """Document split-path: original for diarize, clean for VAD (orchestrator)."""
    from pipeline.orchestrator import run_pipeline

    assert callable(run_pipeline)


def test_init_df_keyword_matches_deepfilternet_api():
    """deepfilternet 0.5.6 uses default_model, not model_name."""
    pytest = __import__("pytest")
    pytest.importorskip("df")
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam")))
    from torchaudio_compat import apply_deepfilter_compat

    apply_deepfilter_compat()
    from df.enhance import init_df
    import inspect

    params = inspect.signature(init_df).parameters
    assert "default_model" in params
    assert "model_name" not in params


def test_denoise_audio_passes_model_base_dir(tmp_path, monkeypatch):
    """denoise_audio() must call init_df with model_base_dir pointing at GIGAAM_DEEPFILTER_DIR."""
    import importlib as _ilib
    import types
    import unittest.mock as mock

    gigaam_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam"))
    if gigaam_root not in sys.path:
        sys.path.insert(0, gigaam_root)

    deepfilter_dir = str(tmp_path / "deepfilter")
    monkeypatch.setenv("GIGAAM_DEEPFILTER_DIR", deepfilter_dir)

    sr = 16000
    audio = np.zeros(sr, dtype=np.float32)
    input_path = str(tmp_path / "in.wav")
    output_path = str(tmp_path / "out.wav")
    sf.write(input_path, audio, sr)

    captured = {}

    class _EarlyExit(Exception):
        pass

    def fake_init_df(**kwargs):
        captured.update(kwargs)
        raise _EarlyExit("stop after capture")

    # Build a fake df hierarchy so apply_deepfilter_compat and init_df don't need the real wheel.
    fake_df = types.ModuleType("df")
    fake_df_utils = types.ModuleType("df.utils")
    fake_df_utils.get_git_root = lambda: None
    fake_df_utils.get_commit_hash = lambda: None
    fake_df_utils.get_branch_name = lambda: None
    fake_df_enhance = types.ModuleType("df.enhance")
    fake_df_enhance.init_df = fake_init_df
    fake_df_enhance.enhance = lambda *a, **k: None
    fake_df_enhance.get_device = lambda: __import__("torch").device("cpu")

    with mock.patch.dict(sys.modules, {
        "df": fake_df,
        "df.utils": fake_df_utils,
        "df.enhance": fake_df_enhance,
    }):
        import pipeline.denoise as _denoise_mod
        # Reload so _DEEPFILTER_DIR is re-read from the patched env.
        _ilib.reload(_denoise_mod)
        try:
            _denoise_mod.denoise_audio(input_path, output_path)
        except _EarlyExit:
            pass
        except Exception:
            pass

    assert "model_base_dir" in captured, (
        f"init_df must be called with model_base_dir=, got kwargs: {captured}"
    )
    assert deepfilter_dir in str(captured["model_base_dir"])


def test_flush_memory_called_on_non_retryable_enhance_failure(tmp_path, monkeypatch):
    """flush_memory must be called even when enhance() raises a non-retryable error (VRAM leak fix)."""
    import importlib as _ilib
    import unittest.mock as mock
    import torch

    gigaam_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam"))
    if gigaam_root not in sys.path:
        sys.path.insert(0, gigaam_root)

    monkeypatch.setenv("GIGAAM_DEEPFILTER_DIR", str(tmp_path))

    sr = 16000
    sf.write(str(tmp_path / "in.wav"), np.zeros(sr, dtype=np.float32), sr)

    flush_calls = []

    def fatal_enhance(model, df_state, wav):
        raise RuntimeError("CUDA out of memory")  # non-retryable — not "device not ready"

    fake_mods = _make_fake_df_modules(fatal_enhance)

    with mock.patch.dict(sys.modules, fake_mods):
        with mock.patch("torch.cuda.is_available", return_value=False):
            import pipeline.denoise as _mod
            _ilib.reload(_mod)
            with mock.patch.object(_mod, "flush_memory", side_effect=lambda h: flush_calls.append(h)):
                try:
                    _mod.denoise_audio(
                        str(tmp_path / "in.wav"),
                        str(tmp_path / "out.wav"),
                    )
                except RuntimeError:
                    pass  # expected

    assert len(flush_calls) >= 1, (
        "flush_memory() must be called on the error path to prevent VRAM leak"
    )


def _make_fake_df_modules(enhance_fn):
    """Build a minimal fake df module hierarchy for denoise tests."""
    import types
    import torch

    class _FakeModel:
        def to(self, device):
            return self
        def cpu(self):
            return self

    def fake_init_df(**kwargs):
        return _FakeModel(), object(), None

    fake_df = types.ModuleType("df")
    fake_df_utils = types.ModuleType("df.utils")
    fake_df_utils.get_git_root = lambda: None
    fake_df_utils.get_commit_hash = lambda: None
    fake_df_utils.get_branch_name = lambda: None
    fake_df_enhance = types.ModuleType("df.enhance")
    fake_df_enhance.init_df = fake_init_df
    fake_df_enhance.enhance = enhance_fn
    fake_df_enhance.get_device = lambda: torch.device("cpu")
    return {"df": fake_df, "df.utils": fake_df_utils, "df.enhance": fake_df_enhance}


def _cuda_zeros_mock(call_order_list, tag="cuda_warmup_alloc"):
    """Return a torch.zeros side-effect that strips device= and records the call."""
    import torch as _torch
    _real = _torch.zeros

    def _mock(*args, **kw):
        if "device" in kw:
            call_order_list.append(tag)
            kw = {k: v for k, v in kw.items() if k != "device"}
        return _real(*args, **kw)

    return _mock


def test_cuda_synchronize_called_after_model_to_gpu(tmp_path, monkeypatch):
    """torch.cuda.synchronize() must be called before the first enhance() when device is GPU."""
    import importlib as _ilib
    import unittest.mock as mock
    import torch

    gigaam_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam"))
    if gigaam_root not in sys.path:
        sys.path.insert(0, gigaam_root)

    monkeypatch.setenv("GIGAAM_DEEPFILTER_DIR", str(tmp_path))

    sr = 16000
    sf.write(str(tmp_path / "in.wav"), np.zeros(sr, dtype=np.float32), sr)
    enhanced_wav = torch.zeros(1, sr * 3)

    call_order = []

    def recording_enhance(model, df_state, wav):
        call_order.append("enhance")
        return enhanced_wav

    fake_mods = _make_fake_df_modules(recording_enhance)

    with mock.patch.dict(sys.modules, fake_mods):
        with mock.patch("torch.cuda.is_available", return_value=True):
            with mock.patch("torch.cuda.synchronize", side_effect=lambda: call_order.append("sync")):
                with mock.patch("torch.cuda.empty_cache"):
                    with mock.patch("torch.zeros", side_effect=_cuda_zeros_mock(call_order)):
                        import pipeline.denoise as _mod
                        _ilib.reload(_mod)
                        _mod.denoise_audio(
                            str(tmp_path / "in.wav"),
                            str(tmp_path / "out.wav"),
                        )

    assert "sync" in call_order, "torch.cuda.synchronize() was never called"
    first_sync = call_order.index("sync")
    first_enhance = call_order.index("enhance")
    assert first_sync < first_enhance, (
        f"synchronize() must be called before the first enhance(); call order: {call_order}"
    )


def test_cuda_context_prewarmed_before_init_df(tmp_path, monkeypatch):
    """CUDA pre-warmup (alloc + kernel + sync) must execute before df.init_df() is called."""
    import importlib as _ilib
    import types
    import unittest.mock as mock
    import torch

    gigaam_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam"))
    if gigaam_root not in sys.path:
        sys.path.insert(0, gigaam_root)

    monkeypatch.setenv("GIGAAM_DEEPFILTER_DIR", str(tmp_path))

    sr = 16000
    sf.write(str(tmp_path / "in.wav"), np.zeros(sr, dtype=np.float32), sr)
    enhanced_wav = torch.zeros(1, sr * 3)

    call_order = []

    class _FakeModel:
        def to(self, device): return self
        def cpu(self): return self

    def recording_init_df(**kwargs):
        call_order.append("init_df")
        return _FakeModel(), object(), None

    fake_df = types.ModuleType("df")
    fake_df_utils = types.ModuleType("df.utils")
    fake_df_utils.get_git_root = lambda: None
    fake_df_utils.get_commit_hash = lambda: None
    fake_df_utils.get_branch_name = lambda: None
    fake_df_enhance = types.ModuleType("df.enhance")
    fake_df_enhance.init_df = recording_init_df
    fake_df_enhance.enhance = lambda model, df_state, wav: enhanced_wav
    fake_df_enhance.get_device = lambda: torch.device("cpu")
    fake_mods = {"df": fake_df, "df.utils": fake_df_utils, "df.enhance": fake_df_enhance}

    with mock.patch.dict(sys.modules, fake_mods):
        with mock.patch("torch.cuda.is_available", return_value=True):
            with mock.patch("torch.cuda.synchronize", side_effect=lambda: call_order.append("sync")):
                with mock.patch("torch.cuda.empty_cache"):
                    with mock.patch("torch.zeros", side_effect=_cuda_zeros_mock(call_order)):
                        import pipeline.denoise as _mod
                        _ilib.reload(_mod)
                        _mod.denoise_audio(
                            str(tmp_path / "in.wav"),
                            str(tmp_path / "out.wav"),
                        )

    assert "cuda_warmup_alloc" in call_order, (
        "Pre-warmup torch.zeros(device=cuda) was not called before init_df()"
    )
    assert "sync" in call_order, "torch.cuda.synchronize() was not called during pre-warmup"
    assert "init_df" in call_order, "init_df was never called"
    first_warmup = call_order.index("cuda_warmup_alloc")
    first_sync = call_order.index("sync")
    first_init_df = call_order.index("init_df")
    assert first_warmup < first_init_df, (
        f"Pre-warmup alloc must precede init_df(); call order: {call_order}"
    )
    assert first_sync < first_init_df, (
        f"Pre-warmup sync must precede init_df(); call order: {call_order}"
    )


# ---------------------------------------------------------------------------
# BUG-004: windowed denoise — chunking, short-file path, quality, duration,
#          and OOM adaptive fallback
# ---------------------------------------------------------------------------

def _run_denoise_cpu(tmp_path, monkeypatch, audio_samples, sr, enhance_fn,
                     chunk_sec=5.0, overlap_sec=0.5):
    """Helper: run denoise_audio() on CPU with fake df modules and injected audio."""
    import importlib as _ilib
    import unittest.mock as mock
    import torch

    gigaam_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam"))
    if gigaam_root not in sys.path:
        sys.path.insert(0, gigaam_root)

    monkeypatch.setenv("GIGAAM_DEEPFILTER_DIR", str(tmp_path))

    input_path = str(tmp_path / "in.wav")
    output_path = str(tmp_path / "out.wav")
    sf.write(input_path, audio_samples.astype(np.float32), sr)

    fake_mods = _make_fake_df_modules(enhance_fn)
    with mock.patch.dict(sys.modules, fake_mods):
        with mock.patch("torch.cuda.is_available", return_value=False):
            import pipeline.denoise as _mod
            _ilib.reload(_mod)
            _mod.denoise_audio(
                input_path,
                output_path,
                chunk_sec=chunk_sec,
                overlap_sec=overlap_sec,
            )
    return output_path


def test_long_audio_is_chunked(tmp_path, monkeypatch):
    """enhance() must be called >1 time when audio is longer than chunk_sec."""
    import importlib as _ilib
    import unittest.mock as mock

    gigaam_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam"))
    if gigaam_root not in sys.path:
        sys.path.insert(0, gigaam_root)

    monkeypatch.setenv("GIGAAM_DEEPFILTER_DIR", str(tmp_path))

    sr_48k = 48000
    chunk_sec = 5.0
    overlap_sec = 0.5
    # 15 s audio → should produce ≥ 3 windows with 5 s chunk, 0.5 s overlap
    audio_16k = np.zeros(int(15 * 16000), dtype=np.float32)
    input_path = str(tmp_path / "in.wav")
    output_path = str(tmp_path / "out.wav")
    sf.write(input_path, audio_16k, 16000)

    window_lengths = []

    def recording_enhance(model, df_state, wav):
        window_lengths.append(wav.shape[-1])
        return wav  # identity

    fake_mods = _make_fake_df_modules(recording_enhance)
    with mock.patch.dict(sys.modules, fake_mods):
        with mock.patch("torch.cuda.is_available", return_value=False):
            import pipeline.denoise as _mod
            _ilib.reload(_mod)
            _mod.denoise_audio(
                input_path,
                output_path,
                chunk_sec=chunk_sec,
                overlap_sec=overlap_sec,
            )

    assert len(window_lengths) > 1, (
        f"enhance() called {len(window_lengths)} time(s); expected >1 for 15s audio with 5s chunks"
    )
    max_window_samples = int((chunk_sec + overlap_sec) * sr_48k) + sr_48k  # generous upper bound
    for i, wlen in enumerate(window_lengths):
        assert wlen <= max_window_samples, (
            f"Window {i} has {wlen} samples ({wlen / sr_48k:.2f}s), exceeds max allowed"
        )


def test_short_audio_single_call(tmp_path, monkeypatch):
    """enhance() must be called exactly once for audio shorter than chunk_sec."""
    import importlib as _ilib
    import unittest.mock as mock

    gigaam_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam"))
    if gigaam_root not in sys.path:
        sys.path.insert(0, gigaam_root)

    monkeypatch.setenv("GIGAAM_DEEPFILTER_DIR", str(tmp_path))

    # 2 s audio, chunk_sec=5 → single call
    audio_16k = np.zeros(int(2 * 16000), dtype=np.float32)
    input_path = str(tmp_path / "in.wav")
    output_path = str(tmp_path / "out.wav")
    sf.write(input_path, audio_16k, 16000)

    call_count = []

    def recording_enhance(model, df_state, wav):
        call_count.append(1)
        return wav

    fake_mods = _make_fake_df_modules(recording_enhance)
    with mock.patch.dict(sys.modules, fake_mods):
        with mock.patch("torch.cuda.is_available", return_value=False):
            import pipeline.denoise as _mod
            _ilib.reload(_mod)
            _mod.denoise_audio(
                input_path,
                output_path,
                chunk_sec=5.0,
                overlap_sec=0.5,
            )

    assert len(call_count) == 1, (
        f"enhance() called {len(call_count)} time(s) for short audio; expected exactly 1"
    )


def test_chunked_output_duration_preserved(tmp_path, monkeypatch):
    """Output WAV duration must match input duration within 50 ms after chunked denoise."""
    import importlib as _ilib
    import unittest.mock as mock

    gigaam_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam"))
    if gigaam_root not in sys.path:
        sys.path.insert(0, gigaam_root)

    monkeypatch.setenv("GIGAAM_DEEPFILTER_DIR", str(tmp_path))

    duration_in = 12.0  # seconds
    audio_16k = np.zeros(int(duration_in * 16000), dtype=np.float32)
    input_path = str(tmp_path / "in.wav")
    output_path = str(tmp_path / "out.wav")
    sf.write(input_path, audio_16k, 16000)

    fake_mods = _make_fake_df_modules(lambda model, df_state, wav: wav)  # identity
    with mock.patch.dict(sys.modules, fake_mods):
        with mock.patch("torch.cuda.is_available", return_value=False):
            import pipeline.denoise as _mod
            _ilib.reload(_mod)
            _mod.denoise_audio(
                input_path,
                output_path,
                chunk_sec=5.0,
                overlap_sec=0.5,
            )

    out_audio, out_sr = sf.read(output_path)
    duration_out = len(out_audio) / out_sr
    assert abs(duration_out - duration_in) < 0.05, (
        f"Duration drift too large: in={duration_in:.3f}s out={duration_out:.3f}s"
    )


def test_chunked_identity_enhance_lossless(tmp_path, monkeypatch):
    """Identity enhance on a constant signal: stitched output must match input closely."""
    import importlib as _ilib
    import unittest.mock as mock

    gigaam_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam"))
    if gigaam_root not in sys.path:
        sys.path.insert(0, gigaam_root)

    monkeypatch.setenv("GIGAAM_DEEPFILTER_DIR", str(tmp_path))

    # Constant signal (all 0.5) — crossfade of identical values is lossless
    duration_in = 12.0
    audio_16k = np.full(int(duration_in * 16000), 0.5, dtype=np.float32)
    input_path = str(tmp_path / "in.wav")
    output_path = str(tmp_path / "out.wav")
    sf.write(input_path, audio_16k, 16000)

    fake_mods = _make_fake_df_modules(lambda model, df_state, wav: wav)
    with mock.patch.dict(sys.modules, fake_mods):
        with mock.patch("torch.cuda.is_available", return_value=False):
            import pipeline.denoise as _mod
            _ilib.reload(_mod)
            _mod.denoise_audio(
                input_path,
                output_path,
                chunk_sec=5.0,
                overlap_sec=0.5,
            )

    out_audio, out_sr = sf.read(output_path)
    # After resample back to 16k the signal should remain ≈ 0.5
    # Trim to min length to avoid boundary edge effects
    min_len = min(len(audio_16k), len(out_audio))
    trim_s = int(0.1 * out_sr)  # skip first/last 100ms (resampler ramp)
    inner = slice(trim_s, min_len - trim_s)
    max_diff = float(np.max(np.abs(out_audio[inner] - audio_16k[inner])))
    assert max_diff < 0.05, (
        f"Max abs difference too large for identity enhance: {max_diff:.4f}"
    )


def test_oom_fallback_splits_and_completes(tmp_path, monkeypatch):
    """On per-window OOM, denoise must halve the window and complete (no RuntimeError raised)."""
    import importlib as _ilib
    import unittest.mock as mock
    import torch

    gigaam_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gigaam"))
    if gigaam_root not in sys.path:
        sys.path.insert(0, gigaam_root)

    monkeypatch.setenv("GIGAAM_DEEPFILTER_DIR", str(tmp_path))

    sr_48k = 48000
    # OOM threshold: raise OOM for windows > 3 s (150 000 samples @ 48k)
    oom_threshold_samples = int(3.0 * sr_48k)

    def oom_enhance(model, df_state, wav):
        if wav.shape[-1] > oom_threshold_samples:
            raise RuntimeError("CUDA out of memory")
        return wav  # succeeds for small windows

    audio_16k = np.zeros(int(10 * 16000), dtype=np.float32)
    input_path = str(tmp_path / "in.wav")
    output_path = str(tmp_path / "out.wav")
    sf.write(input_path, audio_16k, 16000)

    fake_mods = _make_fake_df_modules(oom_enhance)
    with mock.patch.dict(sys.modules, fake_mods):
        with mock.patch("torch.cuda.is_available", return_value=False):
            with mock.patch("torch.cuda.empty_cache"):
                import pipeline.denoise as _mod
                _ilib.reload(_mod)
                # Must not raise — adaptive fallback should handle the OOM
                _mod.denoise_audio(
                    input_path,
                    output_path,
                    chunk_sec=5.0,
                    overlap_sec=0.5,
                )

    assert os.path.exists(output_path), "Output WAV must exist after OOM fallback"
    out_audio, out_sr = sf.read(output_path)
    duration_out = len(out_audio) / out_sr
    assert abs(duration_out - 10.0) < 0.05, (
        f"Duration mismatch after OOM fallback: {duration_out:.3f}s"
    )
