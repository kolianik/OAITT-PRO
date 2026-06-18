# syntax=docker/dockerfile:1
FROM pytorch/pytorch:2.9.1-cuda12.8-cudnn9-runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/data \
    TORCH_HOME=/app/data/torch \
    PYTORCH_ALLOC_CONF=expandable_segments:True \
    GIGAAM_ONNX_DIR=/app/data/gigaam_onnx \
    GIGAAM_WEIGHTS_DIR=/app/data/gigaam \
    GIGAAM_DEEPFILTER_DIR=/app/data/deepfilter \
    LD_LIBRARY_PATH=/opt/conda/lib/python3.11/site-packages/torch/lib:/opt/conda/lib:/opt/conda/lib/python3.11/site-packages/nvidia/cuda_runtime/lib:/opt/conda/lib/python3.11/site-packages/nvidia/cuda_nvrtc/lib:/opt/conda/lib/python3.11/site-packages/nvidia/npp/lib

RUN groupadd -r -g 1001 oaitt && useradd -r -u 1001 -g oaitt -d /app -s /sbin/nologin oaitt

# ---------------------------------------------------------------------------------
# Network scenarios: proxy (S2) + corporate MITM CA (S3).
# Empty/zero defaults keep the direct-internet build (S1) byte-for-byte unchanged.
# The proxy ENV reaches apt/conda/pip/git and the build-time DeepFilterNet download;
# the CA layer is gated (CORP_CA_AUTO_TRUST=1 AND a staged certs/extra-ca/*.crt) and
# runs BEFORE the dependency apt-get update so that install can verify under a MITM proxy.
# ---------------------------------------------------------------------------------
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG NO_PROXY=""
ARG http_proxy=""
ARG https_proxy=""
ARG no_proxy=""
ENV HTTP_PROXY=$HTTP_PROXY HTTPS_PROXY=$HTTPS_PROXY NO_PROXY=$NO_PROXY \
    http_proxy=$http_proxy https_proxy=$https_proxy no_proxy=$no_proxy

ARG CORP_CA_AUTO_TRUST=0
COPY certs/extra-ca/ /usr/local/share/ca-certificates/extra/
RUN if [ "$CORP_CA_AUTO_TRUST" = "1" ] && ls /usr/local/share/ca-certificates/extra/*.crt >/dev/null 2>&1; then \
        ( command -v update-ca-certificates >/dev/null 2>&1 || \
          ( apt-get update && apt-get install -y --no-install-recommends ca-certificates ) ) && \
        update-ca-certificates && \
        echo "Corporate CA(s) trusted in gigaam build store"; \
    else \
        rm -f /usr/local/share/ca-certificates/extra/*.crt 2>/dev/null || true; \
    fi
# Point every TLS-using tool/library (build + runtime) at the merged system bundle.
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    GIT_SSL_CAINFO=/etc/ssl/certs/ca-certificates.crt \
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libsndfile1 \
    libgomp1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && conda config --set channel_priority strict \
    && (conda install -y -c conda-forge 'ffmpeg>=7,<8' || true) \
    && conda clean -ya

COPY gigaam/constraints.txt gigaam/requirements-*.txt gigaam/install_deps.sh ./
RUN chmod +x install_deps.sh && ./install_deps.sh

COPY vendor/gigaam /app/vendor/gigaam
RUN python3 -m pip install --no-cache-dir --no-deps /app/vendor/gigaam && \
    rm -rf /app/vendor/gigaam

COPY shared/ /app/shared/
COPY gigaam/main.py gigaam/bootstrap_state.py gigaam/bootstrap_models.py \
    gigaam/export_onnx.py gigaam/download_models.py gigaam/healthcheck.py \
    gigaam/torchaudio_compat.py gigaam/entrypoint.sh ./
COPY gigaam/pipeline/ /app/pipeline/

# Bake DeepFilterNet3 weights into the image at build time.
# The build host can reach github.com; the runtime container cannot (inference_net is internal).
# Bootstrap copies /opt/deepfilter → volume offline via seed_deepfilter_from_baked().
ARG GIGAAM_DENOISE_MODEL=DeepFilterNet3
ENV GIGAAM_BAKED_DEEPFILTER_DIR=/opt/deepfilter
RUN python3 -c "\
from download_models import download_deepfilter; \
import os; \
model_name = os.environ.get('GIGAAM_DENOISE_MODEL', '${GIGAAM_DENOISE_MODEL}'); \
model_dir = '/opt/deepfilter/DeepFilterNet/' + model_name; \
download_deepfilter(model_name=model_name, model_dir=model_dir)"

RUN chmod +x entrypoint.sh && \
    mkdir -p /shared_data && \
    chown -R oaitt:oaitt /app /shared_data /opt/deepfilter

ENV HOME=/app MPLCONFIGDIR=/tmp/mplconfig

EXPOSE 9007

HEALTHCHECK --interval=30s --timeout=10s --retries=5 --start-period=3600s \
    CMD ["python3", "/app/healthcheck.py"]

ENTRYPOINT ["./entrypoint.sh"]
