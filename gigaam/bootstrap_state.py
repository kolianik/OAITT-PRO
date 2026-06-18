"""Thread-safe bootstrap state for GigaAM model cache readiness."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class BootstrapState:
    ready: bool = False
    status: str = "bootstrapping"
    phase: str = "init"
    step: int = 0
    steps_total: int = 0
    message: str = "Инициализация сервиса..."
    error: Optional[str] = None
    first_install: bool = False
    missing_artifacts: List[str] = field(default_factory=list)
    cached_models: List[str] = field(default_factory=list)
    pyannote_cached: bool = False
    diarization_available: bool = False

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "ready": self.ready,
                "status": self.status,
                "phase": self.phase,
                "step": self.step,
                "steps_total": self.steps_total,
                "message": self.message,
                "error": self.error,
                "first_install": self.first_install,
                "missing_artifacts": list(self.missing_artifacts),
                "cached_models": list(self.cached_models),
                "pyannote_cached": self.pyannote_cached,
                "diarization_available": self.diarization_available,
            }

    def to_health_dict(self, *, device: str, onnx_dir: str, denoise_enabled: bool) -> Dict[str, Any]:
        data = self.snapshot()
        body: Dict[str, Any] = {
            "status": data["status"],
            "ready": data["ready"],
            "message": data["message"],
            "device": device,
            "onnx_dir": onnx_dir,
            "denoise_enabled": denoise_enabled,
        }
        if data["phase"]:
            body["phase"] = data["phase"]
        if data["steps_total"]:
            body["step"] = data["step"]
            body["steps_total"] = data["steps_total"]
        if data["first_install"]:
            body["first_install"] = True
        if data["missing_artifacts"]:
            body["missing_artifacts"] = data["missing_artifacts"]
        if data["ready"] and data["cached_models"]:
            body["cached_models"] = data["cached_models"]
        if data["error"]:
            body["error"] = data["error"]
        body["pyannote_cached"] = data["pyannote_cached"]
        body["diarization_available"] = data["diarization_available"]
        return body


STATE = BootstrapState()
