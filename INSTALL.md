# OAITT-PRO Installation Guide (Ubuntu 24.04 LTS)

This guide describes a reproducible deployment on **Ubuntu Server 24.04 LTS** with an **NVIDIA GPU** and **Docker Compose**. All client-facing hostnames and ports are configured in [`.env`](.env.example) — not hardcoded in application docs.

**Version:** 1.3.0 (see [CHANGELOG.md](CHANGELOG.md))

---

## 1. Requirements

| Component | Minimum |
|-----------|---------|
| **OS** | Ubuntu 24.04 LTS (64-bit) |
| **GPU** | NVIDIA with CUDA support; **12 GB VRAM** recommended (validated on RTX 3060). **10 GB** (e.g. RTX 3080) works with `WHISPERX_BATCH_SIZE` tuning — see [TROUBLESHOOTING.md — Error 5](TROUBLESHOOTING.md#-error-5-cuda-out-of-memory-oom-on-rtx-30603080). |
| **Driver** | Proprietary NVIDIA driver **>= 570.26** for GigaAM (CUDA 12.8 runtime). WhisperX uses a separate image; validate GPU access with the commands in §2.3 |
| **Docker** | Docker Engine 24+ and Compose plugin v2 |
| **Disk** | 100+ GB free SSD (images, model caches; GigaAM base image ~6 GB on first pull) |
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
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi
```

---

## 3. Application artifacts (before `docker compose build`)

### 3.1 Clone the repository

```bash
git clone <your-repo-url>
cd Transcribe_server
```

### 3.2 GigaAM model cache (volume bootstrap)

The `gigaam-service` image contains **dependencies only** (no baked-in model weights). On first `docker compose up`, Compose **creates** the `gigaam_model_cache` volume automatically and mounts it at `/app/data`. The service bootstraps models in the background and exposes progress via `GET /health` and Docker healthcheck (may take **15–60+ minutes** on first run). Gateway shows `gigaam_service: offline` until bootstrap completes.

**Cache inventory (volume `/app/data`):**

| Component | Source | Path |
|-----------|--------|------|
| GigaAM ONNX ASR | export at bootstrap | `/app/data/gigaam_onnx/` |
| Wav2Vec2 alignment | Hugging Face | `/app/data/hub/` |
| Pyannote diarization (optional) | Hugging Face gated | `/app/data/hub/` |
| GigaAM PyTorch (bootstrap only) | Sber CDN | `/app/data/gigaam/` |
| DeepFilterNet3 weights (`GIGAAM_DENOISE=true`) | GitHub release (bootstrap) | `/app/data/deepfilter/DeepFilterNet/<model>/` |

Silero VAD ships inside the pip wheel (no network needed). DeepFilterNet3 **weights** are downloaded from GitHub at bootstrap and stored in the volume under `GIGAAM_DEEPFILTER_DIR/DeepFilterNet/<model>/` (default: `/app/data/deepfilter/DeepFilterNet/DeepFilterNet3/`); the service will not become `healthy` with `GIGAAM_DENOISE=true` until the weights are present. Set `GIGAAM_DEEPFILTER_DIR` to override the root path.

Denoise has **no audio length limit** — it processes the file in overlapping 30-second windows (configurable via `GIGAAM_DENOISE_CHUNK_SEC` / `GIGAAM_DENOISE_OVERLAP_SEC`), keeping peak VRAM under 1 GB regardless of file duration. See [TROUBLESHOOTING.md — Error 6b](TROUBLESHOOTING.md#-error-6b-cuda-out-of-memory-tried-to-allocate-480-gib-during-denoise-on-long-audio) for details.

Set `HF_TOKEN` in `.env` for Pyannote prefetch (`GIGAAM_PREFETCH_DIARIZATION=true`, default). Without a token the service still becomes **healthy** for non-diarize jobs.

**Build** (no `prepare.sh` or build-time `HF_TOKEN` required):

```powershell
.\build-gigaam.ps1
.\start.ps1   # canonical start: daemon preflight + strips loopback HTTP(S)_PROXY
```

Wait until `docker compose ps` shows `gigaam-service` as **healthy** (or inspect Health.Log: `docker inspect --format='{{json .State.Health}}' oaitt-gigaam`).

**Air-gapped / corporate firewall:** see [TROUBLESHOOTING.md](TROUBLESHOOTING.md) §GigaAM model cache, or run `scripts/seed_gigaam_cache.sh` on a machine with internet and import the tar on the GPU host. Set `GIGAAM_OFFLINE_MODE=true` on prod after import.

Optional host prefetch (accelerates bootstrap): `python prepare.py` then seed via `docker compose run --rm -v ./data:/app/data gigaam-service python3 bootstrap_models.py`.

The GigaAM container uses `pytorch/pytorch:2.9.1-cuda12.8-cudnn9-runtime`. **FFmpeg** (conda-forge `>=7,<8` or apt) and **TorchCodec 0.9.1** need **`nvidia-npp-cu12`** and extended `LD_LIBRARY_PATH`.

### 3.3 WhisperX model cache

WhisperX loads ASR weights with `local_files_only=True` and alignment weights with `model_cache_only=True` from `/app/data` (Docker volume `whisperx_model_cache`). Both the ASR model (`WHISPERX_MODEL`) and the Russian Wav2Vec2 alignment model (`WHISPERX_ALIGN_MODEL`, default `jonatasgrosman/wav2vec2-xls-r-1b-russian`) must be present before the first transcription job. Choose one approach:

**Option A — bake at image build (recommended for production):**

In [`whisperx/Dockerfile`](whisperx/Dockerfile), uncomment:

```dockerfile
RUN python download_models.py
```

Then rebuild the image. `HF_TOKEN` is not required for the default Russian ASR or alignment model downloads.

**Option B — populate the Docker volume after first start:**

Start the stack once, then run the downloader inside the container:

```bash
docker compose exec whisperx-service python download_models.py
```

After changing `WHISPERX_ALIGN_MODEL`, re-run `download_models.py` (or rebuild with Option A) so the new alignment weights are cached. The 1B alignment model uses ~3–4 GB on disk.

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

Set `HF_TOKEN` in `.env` (runtime Pyannote prefetch / diarization).

**PowerShell:**
```powershell
.\build-gigaam.ps1
.\start.ps1
```

**Bash:**
```bash
./build-gigaam.sh
./start.sh
```

> **Start via `start.ps1` / `start.sh`, not a bare `docker compose up -d`.** The start scripts do a Docker daemon preflight (a clear message instead of a cryptic HTTP 500 when the WSL2 backend is down) and **strip loopback proxy vars** — `HTTP_PROXY`/`HTTPS_PROXY` pointing at `127.0.0.1`/`localhost` — before invoking Compose. `docker-compose.yml` passes these straight into `gigaam-service`, but a host-local proxy is unreachable from inside the container: a bare `docker compose up -d` would strand the cold-start bootstrap with `[Errno 111] Connection refused`. To route container traffic through a host proxy, set a **non-loopback** URL such as `http://host.docker.internal:<port>` (Compose already maps `host.docker.internal`). See [TROUBLESHOOTING.md — Error 1b](TROUBLESHOOTING.md#-error-1b-docker-commands-return-http-500-crashed-wsl2-backend) and CHANGELOG **BUG-002b**.

On **first start**, wait for `gigaam-service` health **healthy** (model bootstrap into `gigaam_model_cache`). Rebuilding the image does not re-download models if the volume is preserved.

If pip downloads time out during image build:

```bash
DOCKER_BUILDKIT=1 docker compose build --network=host gigaam-service
```

Check health (replace port if you changed `PROXY_PORT_HTTP`):

```bash
curl -s "http://localhost:${PROXY_PORT_HTTP:-80}/health"
```

Expected: `"status": "healthy"` and at least one inference service `"online"`.

---

## 6. Network scenarios & proxies (S1 / S2 / S3)

A clean install must work on three kinds of network. The start/build scripts run
**`scripts/netprep.{sh,ps1}`** automatically, so most of this is hands-off — this section
explains what happens and what you set for each case.

`netprep` resolves one canonical outcome and logs it as `OAITT_PROXY_MODE`:

| `OAITT_PROXY_MODE` | Meaning |
|--------------------|---------|
| `direct` | Internet reachable directly → **proxying disabled** (even if a proxy is configured). |
| `translated` | Only a **loopback** proxy (`127.0.0.1:<port>`) was usable → rewritten to `http://host.docker.internal:<port>` for containers/BuildKit. |
| `passthrough` | A non-loopback proxy was used as-is. |
| `none` | No direct path and no working proxy (offline/air-gapped — seed the cache, see §3.2). |

### S1 — Direct internet (zero config)

```powershell
.\build-gigaam.ps1
.\start.ps1            # logs: OAITT_PROXY_MODE=direct
```
```bash
./build-gigaam.sh
./start.sh
```

Because `gigaam-service` runs on `inference_net` (`internal: true`, no external egress — see
[SECURITY.md](SECURITY.md)), the **first run** that downloads GigaAM/HF weights uses the egress
override, then you switch back to the strict-internal stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.bootstrap.yml up -d gigaam-service
# wait until healthy (docker compose ps), then:
./start.sh            # strict internal; models now cached on the volume (offline)
```

> Verify your host's behaviour once:
> `docker compose run --rm gigaam-service python3 -c "import socket; print(socket.gethostbyname('huggingface.co'))"`.
> If it resolves, your Docker setup already grants egress and the bootstrap override is optional;
> if it raises `Temporary failure in name resolution`, the override (or an offline seed) is required.

### S2 — System proxy only (direct blocked)

Set the system proxy however your OS normally does (Windows *Settings → Proxy*, or
`HTTP_PROXY`/`HTTPS_PROXY`, or Linux `/etc/environment` / apt config). Then just run the scripts —
`netprep` discovers it (including the **Windows WinINET registry / WinHTTP**, which a bare env-var
check misses) and:

- If direct internet actually works, it **disables the proxy** (`direct`).
- A **loopback** proxy (`http://127.0.0.1:<port>`) is auto-rewritten to
  `http://host.docker.internal:<port>` (`translated`) — a `127.0.0.1` proxy is otherwise unreachable
  from inside a container or BuildKit.
- A non-loopback proxy is passed straight through (`passthrough`).

**Docker daemon image pulls** (`postgres`, `pytorch/pytorch`, `ghcr.io/...whisperx`, `nginx`,
`certbot`) go through the **daemon**, not your shell, so they need separate config:

- **Docker Desktop (Windows/WSL2):** *Settings → Resources → Proxies* (it reads WinHTTP).
- **Linux:** `/etc/systemd/system/docker.service.d/http-proxy.conf` with
  `Environment="HTTP_PROXY=..." "HTTPS_PROXY=..." "NO_PROXY=..."`, then
  `sudo systemctl daemon-reload && sudo systemctl restart docker`.
  Note the asymmetry: the **daemon** runs on the host, so a host loopback proxy is reachable directly
  there — do **not** translate it to `host.docker.internal` for the daemon config (translation is only
  for containers/BuildKit). `netprep` prints the exact command when it detects a mismatch.

### S3 — Corporate transparent proxy with certificate substitution (MITM)

The proxy re-signs TLS with a corporate root CA. We **add** that root to the trust store (verification
stays ON) rather than disabling checks. This is gated by two factors so a proxy can never be trusted
silently:

```bash
./detect-corp-ca.sh                 # or .\detect-corp-ca.ps1
#  -> stages certs/extra-ca/corp-root-<fp>.crt and prints its SHA-256 fingerprint
# 1) VERIFY the fingerprint with your IT department.
# 2) Set CORP_CA_AUTO_TRUST=1 in .env
./build-gigaam.sh && ./start.sh
```

With `CORP_CA_AUTO_TRUST=1` **and** a staged `certs/extra-ca/*.crt`, the corporate CA is injected into:
image builds (apt, conda, pip's non-PyPI hosts, git clone, the DeepFilterNet bake), container runtime
(HuggingFace + the GigaAM Sber-CDN download), and host-side `prepare.py`. The **Docker daemon** registry
pulls additionally need the CA in the host trust store — on Docker Desktop the Windows Root store
(often pushed by corporate GPO) covers it; on Linux, `netprep`/`start.sh` print the
`update-ca-certificates` + `systemctl restart docker` command. With the flag left at `0` (default), no
corporate CA is trusted. See [TROUBLESHOOTING.md — Error 2](TROUBLESHOOTING.md)
and [SECURITY.md](SECURITY.md#corporate-tls-interception-mitm).

---

## 7. Smoke test

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

**GigaAM path (with diarization).** The committed helper `scripts/gigaam_smoke.py` submits a `model=gigaam` job, polls it to completion, and validates the result shape against [API_transcriptions.md](API_transcriptions.md):

```bash
python scripts/gigaam_smoke.py --file /path/to/sample.wav \
  --token default-client-key --diarize true --insecure
```

It exits non-zero if the result is missing `text` / `duration` / `segments`, or if `diarize=true` produced no `speaker`. The validator is unit-tested in `tests/test_gigaam_smoke.py`; the “`GET /health` stays responsive during a job” guarantee is covered by `tests/test_gigaam_concurrency.py`. If `diarize=true` returns HTTP 400, set `HF_TOKEN` (see [TROUBLESHOOTING.md](TROUBLESHOOTING.md) §2c).

**Production URLs** (HTTPS):

```text
https://${API_UPLOAD_HOST}:${PROXY_PORT_HTTP}/v1/audio/transcriptions/async
https://${API_PUBLIC_HOST}/v1/audio/transcriptions/status/{job_id}
```

Always route transcription traffic through the **gateway** (nginx → port 9000), not directly to inference containers on port 9007.

---

## 8. Production networking

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

## 9. Related documentation

| Document | Description |
|----------|-------------|
| [README.md](README.md) | Overview and quick reference |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | GPU errors, pip timeouts, OOM, NAT |
| [API_transcriptions.md](API_transcriptions.md) | Async API contract |
| [agents.md](agents.md) | Service topology |
| [ddd.md](ddd.md) | Documentation-driven development |
