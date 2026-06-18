"""GPU/RAM cleanup between pipeline stages."""
from __future__ import annotations

import gc
from typing import Any, Iterable

import torch


def flush_memory(model_vars: Iterable[Any]) -> None:
    """Release model handles and clear CUDA cache."""
    for var in model_vars:
        del var
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
