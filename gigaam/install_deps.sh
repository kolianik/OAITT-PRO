#!/bin/bash
set -euo pipefail

PIP="python3 -m pip install --no-cache-dir --default-timeout=1000 --retries 10 \
  --trusted-host pypi.org --trusted-host files.pythonhosted.org \
  --trusted-host download.pytorch.org"

# Web stack (no torch index)
$PIP -r requirements-web.txt

# ONNX stack — pin onnx+protobuf to avoid resolver backtracking
$PIP -r requirements-onnx.txt

# TorchCodec + NPP from PyTorch index only
$PIP --index-url https://download.pytorch.org/whl/cu128 \
  --extra-index-url https://pypi.org/simple \
  -c constraints.txt \
  torchcodec==0.9.1 nvidia-npp-cu12

# Pyannote pulls lightning stack transitively — do not pin subpackages
$PIP pyannote.audio==4.0.4

# Audio models — pin deepfilternet to stop scipy backtracking
$PIP -r requirements-audio.txt

# HF + numerics + gigaam vendor deps
$PIP -r requirements-ml.txt

python3 -c "import torchcodec; print('torchcodec', torchcodec.__version__)"
