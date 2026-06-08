# OAITT-PRO Installation Guide (Ubuntu 24.04 LTS)

This guide describes a reproducible deployment on **Ubuntu Server 24.04 LTS** with an **NVIDIA GPU** and **Docker Compose**. All client-facing hostnames and ports are configured in [`.env`](.env.example) — not hardcoded in application docs.

**Version:** 1.1.1 (see [CHANGELOG.md](CHANGELOG.md))

---

## 1. Requirements

| Component | Minimum |
|-----------|---------|
| **OS** | Ubuntu 24.04 LTS (64-bit) |
| **GPU** | NVIDIA with CUDA support; **12 GB VRAM** recommended (validated on RTX 3060). **10 GB** (e.g. RTX 3080) works with `WHISPERX_BATCH_SIZE` tuning — see [TROUBLESHOOTING.md — Error 5](TROUBLESHOOTING.md#-error-5-cuda-out-of-memory-oom-on-rtx-30603080). |
| **Driver** | Proprietary NVIDIA driver compatible with **CUDA 12.1** (typically 525+; use 550+ on 24.04) |
| **Docker** | Docker Engine 24+ and Compose plugin v2 |
| **Disk** | 100+ GB free SSD (images, PyTorch wheels, model caches) |
| **RAM** | 32 GB recommended for build and inference |
| **Network** | Outbound HTTPS for PyPI, Hugging Face, PyTorch, and (optional) Let's Encrypt via Cloudflare |

**Quality policy:** inference uses **FP16 (`float16`)** on GPU. Lower-precision quantizations (`int8`, etc.) are **not supported**. To reduce VRAM use, lower `WHISPERX_BATCH_SIZE` in `.env` only.

---

## 2. Host preparation

### 2.1 NVIDIA driver

Install the proprietary driver from Ubuntu or NVIDIA, then verify:

```bash
nvidia-smi
```

### 2.2 Docker Engine and Compose

Install [Docker Engine](https://docs.docker.com/engine/install/ubuntu/) and the [Compose plugin](https://docs.docker.com/compose/install/linux/). Confirm:

```bash
docker compose version
```

### 2.3 NVIDIA Container Toolkit

Follow **[TROUBLESHOOTING.md — Linux (NVIDIA Container Toolkit)](TROUBLESHOOTING.md#-b-linux-nvidia-container-toolkit)** to install and configure the toolkit, then restart Docker:

```bash
sudo systemctl restart docker
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

---

## 3. Application artifacts (before `docker compose build`)

### 3.1 Clone the repository

```bash
git clone <your-repo-url>
cd Transcribe_server
```

### 3.2 GigaAM weights and vendor package

Run the preparation script (clones `vendor/gigaam` if missing and downloads model weights into `data/gigaam/`):

```bash
chmod +x prepare.sh
./prepare.sh
```

This step is **required**. The `gigaam-service` image copies `data/gigaam/` at build time; an empty `data/` directory will cause the build to fail.

### 3.3 WhisperX model cache

WhisperX loads ASR weights with `local_files_only=True`. Ensure models exist in the `whisperx_model_cache` volume before the first transcription job. Choose one approach:

**Option A — bake at image build (recommended for production):**

In [`whisperx/Dockerfile`](whisperx/Dockerfile), uncomment:

```dockerfile
RUN python download_models.py
```

Then rebuild the image. `HF_TOKEN` is not required for the default Russian faster-whisper model download.

**Option B — populate the Docker volume after first start:**

Start the stack once, then run the downloader inside the container:

```bash
docker compose exec whisperx-service python download_models.py
```

---

## 4. Configure `.env`

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

| Variable | Purpose |
|----------|---------|
| `HF_TOKEN` | Hugging Face read token; accept Pyannote license terms |
| `ADMIN_KEY` | Long random admin API key |
| `POSTGRES_PASSWORD` | Strong database password |
| `API_PUBLIC_HOST` | Public hostname for status/analytics (e.g. `oaitt.example.com`) |
| `API_UPLOAD_HOST` | Upload hostname, often `direct.example.com` |
| `PROXY_PORT_HTTP` | Host port for nginx HTTP (uploads). Use `3000` if that is your public port |
| `PROXY_PORT_HTTPS` | Host port for nginx HTTPS (default `443`) |

Optional for automatic wildcard TLS: `CLOUDFLARE_API_TOKEN`, `DOMAIN_NAME`, `EMAIL`.

For GPUs with **10 GB VRAM**, start with `WHISPERX_BATCH_SIZE=4` and reduce to `2` or `1` if you see CUDA OOM — never change `WHISPERX_COMPUTE_TYPE` away from `float16`.

---

## 5. Build and start

```bash
docker compose up -d --build
```

If PyTorch wheel downloads time out during build:

```bash
docker compose build --network=host
docker compose up -d
```

Check health (replace port if you changed `PROXY_PORT_HTTP`):

```bash
curl -s "http://localhost:${PROXY_PORT_HTTP:-80}/health"
```

Expected: `"status": "healthy"` and at least one inference service `"online"`.

---

## 6. Smoke test

Use values from your `.env`. Example with defaults:

```bash
# Upload (large files) — use API_UPLOAD_HOST and PROXY_PORT_HTTP
curl -X POST "http://localhost:${PROXY_PORT_HTTP:-80}/v1/audio/transcriptions/async" \
  -H "Authorization: Bearer default-client-key" \
  -F "file=@/path/to/sample.wav" \
  -F "model=whisperx" \
  -F "diarize=false"

# Status — via API_PUBLIC_HOST in production (localhost for local test)
curl -s "http://localhost:${PROXY_PORT_HTTP:-80}/v1/audio/transcriptions/status/<job_id>" \
  -H "Authorization: Bearer default-client-key"
```

Replace `default-client-key` with your client key after [production hardening](SECURITY.md).

**Production URLs** (HTTPS):

```text
https://${API_UPLOAD_HOST}:${PROXY_PORT_HTTP}/v1/audio/transcriptions/async
https://${API_PUBLIC_HOST}/v1/audio/transcriptions/status/{job_id}
```

Always route transcription traffic through the **gateway** (nginx → port 9000), not directly to inference containers on port 9007.

---

## 7. Production networking

Docker Compose micro-segments traffic into three networks (see [SECURITY.md — Docker Network Segmentation](SECURITY.md#docker-network-segmentation)):

- **`edge_net`** — only `front-proxy` is reachable from the internet (`PROXY_PORT_HTTP`, `PROXY_PORT_HTTPS`).
- **`backend_net`** (internal) — nginx, gateway, PostgreSQL.
- **`inference_net`** (internal) — gateway, WhisperX, GigaAM. Model containers cannot reach postgres or the public edge.

The gateway host port is **not** published by default. Route all client traffic through nginx. For local debugging: `docker compose -f docker-compose.yml -f docker-compose.debug.yml up` exposes `${GATEWAY_PORT:-9000}`.

Set `INTERNAL_SERVICE_TOKEN` in `.env` (generate with `openssl rand -hex 32`) so inference `/local` and `/unload` reject requests without the gateway's internal header.

Additional hardening:

- **NAT / single open port:** configure `PROXY_PORT_HTTPS` and Cloudflare Origin Rules — see [TROUBLESHOOTING.md — NAT & Cloudflare](TROUBLESHOOTING.md#-3-nat--single-port-deployment-via-cloudflare).
- **Firewall:** expose only `PROXY_PORT_HTTP` / `PROXY_PORT_HTTPS`; block direct access to inference ports.
- **Secrets:** never commit `.env`; rotate keys per [SECURITY.md](SECURITY.md).

---

## 8. Related documentation

| Document | Description |
|----------|-------------|
| [README.md](README.md) | Overview and quick reference |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | GPU errors, pip timeouts, OOM, NAT |
| [API_transcriptions.md](API_transcriptions.md) | Async API contract |
| [agents.md](agents.md) | Service topology |
| [ddd.md](ddd.md) | Documentation-driven development |
