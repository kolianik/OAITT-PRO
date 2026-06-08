# OAITT-PRO — Open AI Transformer Transcriber PRO

**Version:** 1.1.1

Highly optimized speech-to-text service with advanced speaker diarization and dynamic VRAM management on a **single NVIDIA GPU** in **FP16**. Validated on **RTX 3060 (12 GB VRAM)**; **RTX 3080 (10 GB)** is supported with `WHISPERX_BATCH_SIZE` tuning (see [INSTALL.md](INSTALL.md) and [TROUBLESHOOTING.md](TROUBLESHOOTING.md)).

---

## Quick Start

For a full Ubuntu 24.04 LTS install guide, see **[INSTALL.md](INSTALL.md)**.

1. **Clone, prepare models, and configure environment**

```bash
git clone <your-repo-url>
cd Transcribe_server
./prepare.sh
cp .env.example .env
```

Edit `.env`: set `HF_TOKEN`, `ADMIN_KEY`, `POSTGRES_PASSWORD`, `API_PUBLIC_HOST`, `API_UPLOAD_HOST`, and ports. See [SECURITY.md](SECURITY.md) before production use.

2. **Start the stack** (requires Docker, NVIDIA Container Toolkit, and a CUDA-capable GPU)

```bash
docker compose up -d --build
```

3. **Check health**

```bash
curl -s "http://localhost:${PROXY_PORT_HTTP:-80}/health"
```

4. **Submit a transcription job**

```bash
curl -X POST "http://localhost:${PROXY_PORT_HTTP:-80}/v1/audio/transcriptions/async" \
  -H "Authorization: Bearer <your_api_key>" \
  -F "file=@/path/to/audio.wav" \
  -F "model=whisperx" \
  -F "diarize=false"
```

On first run with an empty `clients` table, the gateway seeds a development client key `default-client-key`. **Change or disable this before exposing the API publicly.**

---

## Documentation Index

| Document | Description |
|:---|:---|
| [INSTALL.md](INSTALL.md) | Full installation (Ubuntu 24.04 LTS, GPU, prepare, `.env`) |
| [API_transcriptions.md](API_transcriptions.md) | Async upload and status endpoints |
| [API_analytics.md](API_analytics.md) | Usage analytics and cost estimates |
| [API_admin_pricing.md](API_admin_pricing.md) | Admin tariff management |
| [API_admin_analytics.md](API_admin_analytics.md) | Per-client admin analytics |
| [API_health.md](API_health.md) | Health check |
| [agents.md](agents.md) | Microservices topology and database schema |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | GPU errors, OOM, NAT & Cloudflare |
| [ddd.md](ddd.md) | Documentation-driven development manifest |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| [SECURITY.md](SECURITY.md) | Secrets handling and credential rotation |

---

## Key Features

*   **Dual ASR Engines:** 
    *   **WhisperX:** Utilizing highly-optimized `bzikst/faster-whisper-large-v3-russian` in FP16.
    *   **GigaAM:** Utilizing `v3_e2e_rnnt` with integrated punctuation.
*   **State-of-the-Art Diarization:** Powered by **Pyannote.audio v4.0.4** (`pyannote/speaker-diarization-community-1`).
*   **Dynamic VRAM Management:** Automatically unloads unused models to allow sharing a single GPU without out-of-memory errors.
*   **Asynchronous Job Architecture:** Completely immune to connection timeouts and memory bloating on files up to several gigabytes.
*   **Multi-Client Support:** Built-in PostgreSQL-backed API key management and per-client analytics.
*   **Anti-Hallucination Filters:** Restrict low-confidence transcriptions based on `avg_logprob` and `chars_per_second` thresholds.

---

## API Reference (Summary)

All authenticated requests must include:

```http
Authorization: Bearer <your_api_key>
```

Use hostnames and ports from your `.env` (`API_PUBLIC_HOST`, `API_UPLOAD_HOST`, `PROXY_PORT_HTTP`, `PROXY_PORT_HTTPS`).

### 1. Submit Transcription Job (Asynchronous)

Large file uploads use the upload hostname and HTTP port (bypass CDN body-size limits when `API_UPLOAD_HOST` is DNS-only).

*   **URL:** `https://${API_UPLOAD_HOST}:${PROXY_PORT_HTTP}/v1/audio/transcriptions/async`
*   **Method:** `POST`
*   **Content-Type:** `multipart/form-data`
*   **Docs:** [API_transcriptions.md](API_transcriptions.md)

### 2. Get Job Status & Results

*   **URL:** `https://${API_PUBLIC_HOST}/v1/audio/transcriptions/status/{job_id}`
*   **Method:** `GET`
*   **Query:** `output=json|text|srt|vtt|tsv`

### 3. Analytics Dashboard

*   **URL:** `https://${API_PUBLIC_HOST}/api/v1/analytics/summary`
*   **Docs:** [API_analytics.md](API_analytics.md)

### 4. Admin: Pricing & Per-Client Analytics

Requires `ADMIN_KEY`. See [API_admin_pricing.md](API_admin_pricing.md) and [API_admin_analytics.md](API_admin_analytics.md).

### 5. Health Check

*   **URL:** `https://${API_PUBLIC_HOST}/health`
*   **Docs:** [API_health.md](API_health.md)

---

## Configuration & Secrets

The system is configured via environment variables in `.env` (template: [.env.example](.env.example)):

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_DB` | `transcribe_db` | Postgres database name |
| `POSTGRES_USER` | `postgres` | Postgres username |
| `POSTGRES_PASSWORD` | `secure_pass` | Postgres password — **change in production** |
| `HF_TOKEN` | *required* | Hugging Face access token (Pyannote / models) |
| `ADMIN_KEY` | *required* | Global administrative API key — **change in production** |
| `DOMAIN_NAME` | — | Base domain for Certbot wildcard TLS |
| `API_PUBLIC_HOST` | — | Public hostname for status, analytics, health |
| `API_UPLOAD_HOST` | — | Hostname for large uploads (e.g. `direct.example.com`) |
| `PROXY_PORT_HTTPS` | `443` | HTTPS port on the host |
| `PROXY_PORT_HTTP` | `80` | HTTP port on the host (set to `3000` if that is your public upload port) |
| `GATEWAY_PORT` | `9000` | Gateway host port (only with `docker-compose.debug.yml`) |
| `INTERNAL_SERVICE_TOKEN` | — | Shared secret for gateway → inference calls (`openssl rand -hex 32`) |
| `WHISPERX_MODEL` | `bzikst/faster-whisper-large-v3-russian` | WhisperX model |
| `GIGAAM_MODEL` | `v3_e2e_rnnt` | GigaAM model |
| `DEVICE` | `cuda` | `cuda` or `cpu` |
| `WHISPERX_COMPUTE_TYPE` | `float16` | **CUDA: float16 only** (int8 not supported) |
| `WHISPERX_BATCH_SIZE` | `4` | Transcribe batch size; lower to reduce VRAM without changing model quality |

Never commit `.env` or operational scripts with real credentials. See [SECURITY.md](SECURITY.md).

---

## NAT & Single-Port Deployment (Cloudflare CDN)

If you are running behind NAT with a single open port, set `PROXY_PORT_HTTPS` in `.env` and use Cloudflare **Origin Rules** to route HTTPS (443) to your custom port.

See **[TROUBLESHOOTING.md — NAT & Cloudflare](TROUBLESHOOTING.md#-3-nat--single-port-deployment-via-cloudflare)**.

---

## Development & Tests

```bash
pip install -r tests/requirements.txt
pytest tests/ -q
```

---

## Inspiration & Credits

This project was inspired by and built upon the excellent work of the **[oaitt](https://github.com/haiodo/oaitt)** repository. Special thanks to the original creators for laying down the foundation for high-performance speech transcription and orchestration!
