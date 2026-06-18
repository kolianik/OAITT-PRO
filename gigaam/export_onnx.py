"""Export GigaAM v3_e2e_rnnt to ONNX FP16 (fp32 trace on CPU, then convert)."""
from __future__ import annotations

import os
import sys

import torch

RNNT_SUFFIXES = ("_encoder", "_decoder", "_joint")


def onnx_artifact_names(model: str) -> list[str]:
    return [f"{model}{suffix}.onnx" for suffix in RNNT_SUFFIXES] + [f"{model}.yaml"]


def onnx_complete(onnx_dir: str, model: str) -> bool:
    return all(os.path.isfile(os.path.join(onnx_dir, name)) for name in onnx_artifact_names(model))


def export_onnx(
    *,
    weights_dir: str,
    onnx_dir: str,
    model: str | None = None,
) -> None:
    model = model or os.environ.get("GIGAAM_MODEL", "v3_e2e_rnnt")
    os.makedirs(onnx_dir, exist_ok=True)
    import gigaam

    print(f"Loading GigaAM {model} from {weights_dir}...")
    loaded = gigaam.load_model(
        model,
        fp16_encoder=False,
        use_flash=False,
        device="cpu",
        download_root=weights_dir,
    )
    print(f"Exporting ONNX fp32 to {onnx_dir}...")
    loaded.to_onnx(dir_path=onnx_dir, dtype=torch.float32)
    del loaded

    parts = [f"{model}{suffix}" for suffix in RNNT_SUFFIXES]
    fp16 = os.environ.get("GIGAAM_ONNX_FP16", "false").lower() in ("1", "true", "yes")
    if not fp16:
        print("Keeping fp32 ONNX (GIGAAM_ONNX_FP16=false)")
    try:
        import onnx
        from onnxconverter_common import float16

        if not fp16:
            raise ImportError("fp16 conversion disabled")
        for name in parts:
            path = os.path.join(onnx_dir, f"{name}.onnx")
            if not os.path.isfile(path):
                print(f"Skip fp16 convert (missing): {path}")
                continue
            print(f"Converting {name} to fp16...")
            m = onnx.load(path)
            onnx.save(float16.convert_float_to_float16(m), path)
    except ImportError as exc:
        print(f"WARNING: onnxconverter_common not available ({exc}); keeping fp32 ONNX")

    yaml_path = os.path.join(onnx_dir, f"{model}.yaml")
    if not os.path.isfile(yaml_path):
        raise FileNotFoundError(f"missing model config {yaml_path}")
    print("ONNX export complete.")


def main() -> None:
    onnx_dir = os.environ.get("GIGAAM_ONNX_DIR", "/app/data/gigaam_onnx")
    model = os.environ.get("GIGAAM_MODEL", "v3_e2e_rnnt")
    weights = os.environ.get("GIGAAM_WEIGHTS_DIR", "/app/data/gigaam")
    try:
        export_onnx(weights_dir=weights, onnx_dir=onnx_dir, model=model)
    except Exception as exc:
        print(f"ERROR: ONNX export failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
