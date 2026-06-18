"""HuggingFace cache helpers for offline model loading."""
from __future__ import annotations

import os
from typing import Any, Dict


def is_hub_offline() -> bool:
    return os.environ.get("HF_HUB_OFFLINE", "").strip() in {"1", "true", "yes"}


def hub_load_kwargs(*, cache_dir: str, local_only: bool | None = None) -> Dict[str, Any]:
    offline = is_hub_offline() if local_only is None else local_only
    return {
        "cache_dir": cache_dir,
        "local_files_only": offline,
    }
