# OAITT-PRO — Open AI Transformer Transcriber PRO

**Version:** 1.1.0

Highly optimized speech-to-text service with advanced speaker diarization and dynamic VRAM management, designed specifically for NVIDIA RTX 3060 (12GB VRAM).

---

## Quick Start

1. **Clone and configure environment**

```bash
git clone <your-repo-url>
cd Transcribe_server
cp .env.example .env
```

Edit `.env`: set `HF_TOKEN`, a strong `ADMIN_KEY`, and `POSTGRES_PASSWORD`. See [SECURITY.md](SECURITY.md) before production use.

2. **Start the stack** (requires Docker, NVIDIA Container Toolkit, and a CUDA-capable GPU)

```bash
docker compose up -d --build
```

3. **Check health**

```bash
curl -s http://localhost:${PROXY_PORT_HTTP:-80}/health
```

4. **Submit a transcription job** (replace host/port with your deployment)

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
| [API_transcriptions.md](API_transcriptions.md) | Async upload and status endpoints |
| [API_analytics.md](API_analytics.md) | Usage analytics and cost estimates (v1.1.0) |
| [API_admin_pricing.md](API_admin_pricing.md) | Admin tariff management (v1.1.0) |
| [API_admin_analytics.md](API_admin_analytics.md) | Per-client admin analytics (v1.1.0) |
| [API_health.md](API_health.md) | Health check |
| [agents.md](agents.md) | Microservices topology and database schema |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Installation, GPU, NAT & Cloudflare |
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

Replace `<your-domain>` and ports with values from your `.env` (`DOMAIN_NAME`, `PROXY_PORT_HTTP`, `PROXY_PORT_HTTPS`).

### 1. Submit Transcription Job (Asynchronous)

Submit large files via the unproxied upload port (often `3000` on the host) to bypass CDN body-size limits.

*   **URL:** `https://direct.<your-domain>:3000/v1/audio/transcriptions/async`
*   **Method:** `POST`
*   **Content-Type:** `multipart/form-data`
*   **Docs:** [API_transcriptions.md](API_transcriptions.md)

### 2. Get Job Status & Results

*   **URL:** `https://<your-domain>/v1/audio/transcriptions/status/{job_id}`
*   **Method:** `GET`
*   **Query:** `output=json|text|srt|vtt|tsv`

### 3. Analytics Dashboard (v1.1.0)

*   **URL:** `https://<your-domain>/api/v1/analytics/summary`
*   **Docs:** [API_analytics.md](API_analytics.md)

### 4. Admin: Pricing & Per-Client Analytics (v1.1.0)

Requires `ADMIN_KEY`. See [API_admin_pricing.md](API_admin_pricing.md) and [API_admin_analytics.md](API_admin_analytics.md).

### 5. Health Check

*   **URL:** `https://<your-domain>/health`
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
| `PROXY_PORT_HTTPS` | `443` | HTTPS port on the host |
| `PROXY_PORT_HTTP` | `80` | HTTP port on the host |
| `GATEWAY_PORT` | `9000` | Gateway port on the host |
| `WHISPERX_MODEL` | `bzikst/faster-whisper-large-v3-russian` | WhisperX model |
| `GIGAAM_MODEL` | `v3_e2e_rnnt` | GigaAM model |
| `DEVICE` | `cuda` | `cuda` or `cpu` |
| `WHISPERX_COMPUTE_TYPE` | `float16` | WhisperX quantization |

Never commit `.env` or operational scripts with real credentials. See [SECURITY.md](SECURITY.md).

---

## NAT & Single-Port Deployment (Cloudflare CDN)

If you are running behind NAT with a single open port, use Cloudflare **Origin Rules** to route HTTPS (443) to your custom port.

See **[TROUBLESHOOTING.md — NAT & Cloudflare](TROUBLESHOOTING.md#3-nat--single-port-deployment-via-cloudflare)**.

---

## Development & Tests

```bash
pip install -r tests/requirements.txt
pytest tests/ -q
```

---

## Inspiration & Credits

This project was inspired by and built upon the excellent work of the **[oaitt](https://github.com/haiodo/oaitt)** repository. Special thanks to the original creators for laying down the foundation for high-performance speech transcription and orchestration!
