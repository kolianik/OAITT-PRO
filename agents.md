# Agents & Microservices Specification (agents.md)

This file defines the system architecture, component roles, interface contracts, and communication protocols for the OAITT-PRO transcription system.

---

## 1. System Topology

OAITT-PRO is structured as a multi-agent system of 5 containerized services running on a single NVIDIA GPU host (validated on RTX 3060 12 GB VRAM; RTX 3080 10 GB with `WHISPERX_BATCH_SIZE` tuning).  
To bypass Cloudflare body size (100MB) limits, uploads use **`API_UPLOAD_HOST`** and host port **`PROXY_PORT_HTTP`** from `.env` (DNS-only / unproxied). Status, analytics, and health use **`API_PUBLIC_HOST`** (typically via Cloudflare on port 443).

**Network segmentation** (Docker Compose): `edge_net` (internet → nginx only), `backend_net` internal (nginx ↔ gateway ↔ postgres), `inference_net` internal (gateway ↔ whisperx/gigaam). Cleartext HTTP is used only on internal networks; TLS terminates at nginx.

```
[Client] ──(Upload: API_UPLOAD_HOST:PROXY_PORT_HTTP)──► [Front Nginx Proxy]
[Client] ──(Query: API_PUBLIC_HOST / :443)──► [Cloudflare] ──► [Front Nginx Proxy]
                                      │
                                      ▼
                           [API Orchestrator Gateway] <───► [PostgreSQL 17]
                                  │       │
             (Shared Volume)      │       │     (Shared Volume)
             `/shared_data/` ◄────┘       └────► `/shared_data/`
                    │                                   │
                    ▼                                   ▼
            [WhisperX Service]                  [GigaAM Service]
           (large-v3-russian, FP16)              (v3_e2e_rnnt)
```

---

## 2. Agent Definitions & Contracts

### 2.1. Front Nginx Proxy (Security & TLS Agent)
*   **Role:** Exposes host ports `PROXY_PORT_HTTP` and `PROXY_PORT_HTTPS` (mapped to container 80/443).
*   **SSL Management:** Certbot sidecar running the Cloudflare DNS-01 challenge.
*   **Config Contract:**
    *   Host ports: `${PROXY_PORT_HTTP}:80`, `${PROXY_PORT_HTTPS}:443` (from `.env`)
    *   Target: `http://gateway-orchestrator:9000` (backend_net only)
    *   SSL Cert Storage: Shared Docker volume `/etc/letsencrypt`
    *   `client_max_body_size`: `2048M` (large uploads via `API_UPLOAD_HOST` / `PROXY_PORT_HTTP`)

---

### 2.2. PostgreSQL 17 Database (State & Analytics Agent)
*   **Role:** Persist API clients, usage logs, active transcription jobs, and analytics.
*   **Schema Specification:**

#### Table: `clients`
```sql
CREATE TABLE clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    api_key VARCHAR(255) UNIQUE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_clients_api_key ON clients(api_key);
```

#### Table: `transcription_jobs`
```sql
CREATE TABLE transcription_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    filename VARCHAR(255) NOT NULL,
    file_path VARCHAR(512) NOT NULL,
    engine VARCHAR(50) NOT NULL, -- 'whisperx' | 'gigaam'
    model_name VARCHAR(100) NOT NULL,
    diarization_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    language VARCHAR(10),
    response_format VARCHAR(50) DEFAULT 'json',
    min_avg_logprob NUMERIC(5, 2),
    max_chars_per_second NUMERIC(5, 2),
    webhook_url TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'pending', -- 'pending' | 'processing' | 'completed' | 'failed' | 'hallucination_filtered'
    result JSONB,
    error_message TEXT
);
CREATE INDEX idx_jobs_status ON transcription_jobs(status);
```

#### Table: `transcription_logs` (For Analytics)
```sql
CREATE TABLE transcription_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    filename VARCHAR(255) NOT NULL,
    audio_duration_seconds NUMERIC(10, 2) NOT NULL,
    speech_duration_seconds NUMERIC(10, 2) NOT NULL,
    processing_time_seconds NUMERIC(10, 2) NOT NULL,
    engine VARCHAR(50) NOT NULL, -- 'whisperx' | 'gigaam'
    model_name VARCHAR(100) NOT NULL,
    diarization_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    char_count INTEGER NOT NULL,
    status VARCHAR(50) NOT NULL, -- 'success' | 'failed' | 'timeout' | 'hallucination_filtered'
    error_message TEXT
);
CREATE INDEX idx_logs_client_id ON transcription_logs(client_id);
CREATE INDEX idx_logs_created_at ON transcription_logs(created_at);
```

#### Table: `client_pricing` (Billing Tariffs)
```sql
CREATE TABLE client_pricing (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    audio_price_per_minute NUMERIC(10, 4),
    speech_price_per_minute NUMERIC(10, 4),
    currency VARCHAR(3) NOT NULL DEFAULT 'RUB',
    valid_from DATE NOT NULL DEFAULT CURRENT_DATE,
    valid_to DATE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (client_id, currency, valid_from)
);
CREATE INDEX idx_pricing_client_dates ON client_pricing (client_id, valid_from, valid_to);
```

Both price fields are nullable; `NULL` means that billing dimension is not configured.

---

### 2.3. API Orchestrator (Gateway Agent)
*   **Role:** Single entry point. Handles client authentication, streams media uploads to the shared volume `/shared_data`, maintains an atomic task queue in PostgreSQL, coordinates exclusive VRAM access via `asyncio.Lock`, post-filters hallucinations, fires webhooks, and performs 24-hour job/file cleanup.
*   **Concurrency & VRAM Exclusivity Rules:**
    1.  Maintains an internal `asyncio.Lock` to guarantee that only one GPU job executes at a time.
    2.  Tracks the currently active GPU service: `current_gpu_engine` (`whisperx` or `gigaam`).
    3.  If a request comes for `gigaam` and `current_gpu_engine == "whisperx"`, send `POST http://whisperx-service:9007/unload` to free memory. Then route the job to `gigaam-service`.
    4.  If a request comes for `whisperx` and `current_gpu_engine == "gigaam"`, send `POST http://gigaam-service:9007/unload` to free memory. Then route the job to `whisperx-service`.

*   **Background Worker:**
    *   Polls the database atomically using `SELECT ... FOR UPDATE SKIP LOCKED` for `pending` jobs.
    *   Calls the backend service using a shared path-based JSON payload, bypassing internal HTTP byte transfers.
    *   Saves final JSON outputs to `transcription_jobs.result` and logs success to `transcription_logs`.
    *   **Strict Cleanup:** Guarantees deletion of `/shared_data/{job_id}.{ext}` in a `finally` block immediately after processing ends.
*   **Garbage Collector:**
    *   Runs hourly to clean up DB records older than 24 hours and delete any orphaned files in `/shared_data` older than 24 hours.

*   **API Router Contracts:**
    *   `POST /v1/audio/transcriptions/async` (Upload file, starts async task, returns `202 Accepted` with `job_id`).
    *   `GET /v1/audio/transcriptions/status/{job_id}` (Query task status. Supports query param `?output=json|text|srt|vtt|tsv`).
    *   `GET /api/v1/analytics/summary` (Analytics dashboard; optional `?from=&to=`; admin optional `?client_id=`; includes `cost`).
    *   `POST /api/v1/admin/pricing` (Create client tariff; admin only).
    *   `GET /api/v1/admin/pricing` (Active tariff for client; admin only).
    *   `GET /api/v1/admin/pricing/history` (Tariff history; admin only).
    *   `GET /api/v1/admin/analytics/by-client` (Per-client analytics for date range; admin only).
    *   `GET /health` (System monitoring).

---

### 2.4. WhisperX Service (WhisperX Agent)
*   **Role:** High-accuracy Whisper-based transcription + phoneme alignment + Pyannote v4 diarization.
*   **ASR model:** `bzikst/faster-whisper-large-v3-russian` in `float16` precision (`WHISPERX_MODEL`).
*   **Alignment model:** `jonatasgrosman/wav2vec2-xls-r-1b-russian` (`WHISPERX_ALIGN_MODEL`); loaded from `/app/data` with `model_cache_only=True`.
*   **Diarization Pipeline:** `pyannote/speaker-diarization-community-1` (requires Pyannote v4).
*   **Endpoints:**
    *   `POST /v1/audio/transcriptions/local`: Takes a JSON payload with `file_path`, loads the audio from the shared volume, and performs transcription, alignment, and diarization.
    *   `POST /unload`: Deletes the pipeline, forces PyTorch garbage collection, and runs `torch.cuda.empty_cache()` to free 100% of VRAM.

---

### 2.5. GigaAM Service (GigaAM Agent)
*   **Role:** Russian ASR via ONNX GigaAM + Silero VAD chunking + Wav2Vec2 forced alignment + optional Pyannote v4 diarization with IoW backchannel merge.
*   **Base image:** `pytorch/pytorch:2.9.1-cuda12.8-cudnn9-runtime` (PyTorch 2.9.1 + CUDA 12.8; host driver >= 570.26).
*   **ffmpeg:** conda-forge `ffmpeg>=7,<8` or apt `ffmpeg` (TorchCodec 0.9.1 compatible). **TorchCodec 0.9.1** + `nvidia-npp-cu12` + extended `LD_LIBRARY_PATH`.
*   **Model cache:** Docker volume `gigaam_model_cache` mounted at `/app/data`. On first start the service bootstraps ONNX ASR, Wav2Vec2 alignment, DeepFilterNet3 weights (when `GIGAAM_DENOISE=true`), and (optionally) Pyannote into the volume; manifest at `/app/data/.gigaam/manifest.json`. Silero VAD ships inside the pip wheel (no network needed). DeepFilterNet3 **weights** are **baked into the image** at build time into `/opt/deepfilter/` (`GIGAAM_BAKED_DEEPFILTER_DIR`, default `/opt/deepfilter`) — this avoids any runtime GitHub access, which is not possible from `inference_net` (internal network, no external DNS). Bootstrap seeds them from `/opt/deepfilter/` to the volume offline via `seed_deepfilter_from_baked()`; the network download path is a fallback only. After bootstrap: `HF_HUB_OFFLINE=1`, `local_files_only=True` for HF models; denoise runs fully offline from the volume path. If `download_deepfilter` fails (both offline seed and network), bootstrap continues with denoise gracefully disabled — weights remain absent and the step retries on next container start. Each download step retries transient failures with exponential backoff (`GIGAAM_BOOTSTRAP_RETRIES`, default 3; `GIGAAM_BOOTSTRAP_RETRY_DELAY`, default 5s) before the attempt ends — so a single network blip on first start does not strand the container (restart re-runs bootstrap idempotently against the manifest).
*   **ASR model:** `v3_e2e_rnnt` ONNX **FP32** at `/app/data/gigaam_onnx` (exported at bootstrap from Sber CDN PyTorch weights). FP16 is **opt-in** via `GIGAAM_ONNX_FP16=true` (converts on CPU; silently keeps FP32 if `onnxconverter-common` is unavailable).
*   **Alignment model:** `jonatasgrosman/wav2vec2-xls-r-1b-russian` (`GIGAAM_ALIGN_MODEL`); chunked CTC forced alignment via `transformers` 5.x + `torchaudio.functional.forced_align`. Frames are mapped to words **by target position** via `torchaudio.functional.merge_tokens` (one `TokenSpan` per target token, in order) — never by token-ID, which would scramble word order and drop most words per chunk. Alignment **preserves all ASR text**: words whose chars are out-of-vocabulary (e.g. `B2B-`, `ЦПР`) are time-interpolated between aligned neighbours, and if `forced_align` cannot run (e.g. `target_len > frame_len`) the chunk's words are distributed evenly rather than dropped. Word-level confidence filtering is **not** applied here (low-confidence words are tagged `low_conf` and emitted); anti-hallucination filtering is the gateway's job (`min_avg_logprob`/`max_chars_per_second`).
*   **Denoise (optional):** DeepFilterNet3 on GPU (`GIGAAM_DENOISE=true`); 48 kHz inference, resample to 16 kHz for ASR path. Processed in overlapping windows (`GIGAAM_DENOISE_CHUNK_SEC=30`, `GIGAAM_DENOISE_OVERLAP_SEC=2`) with linear crossfade — peak VRAM ~1 GB regardless of audio length; per-window OOM triggers adaptive halve-and-retry with CPU fallback.
*   **Diarization:** `pyannote/speaker-diarization-community-1` on **original** 16 kHz audio (split-path). Prefetch at bootstrap when `GIGAAM_PREFETCH_DIARIZATION=true` and `HF_TOKEN` set. **Diarization requires `HF_TOKEN` at runtime.** If `GIGAAM_PREFETCH_DIARIZATION=true` but `HF_TOKEN` is empty, the service still becomes `healthy` for transcription, logs a `WARNING`, and reports `diarization_available=false` (with `pyannote_cached`) in `/health`. A `POST /transcriptions*` with `diarize=true` on a deployment without diarization is rejected **early** with HTTP **400** (`detail`: diarization unavailable), instead of failing deep in the pipeline as an opaque 500. Invariant: a `ready` service with `HF_TOKEN` set always has Pyannote cached (otherwise bootstrap fails), so it can always diarize.
*   **Health & concurrency:** `GET /health` returns **503** with Russian `message` while bootstrapping; Docker `healthcheck` prints the same to Health.Log. `POST /transcriptions*` blocked until `ready=true`. Each transcription runs **off the event loop** via `asyncio.to_thread`, so `GET /health` stays responsive while a job is in flight (the Docker healthcheck does not flap during long files). A process-wide `asyncio.Lock` serializes pipeline execution (**single-flight**): only one transcription touches the GPU at a time, protecting the shared per-stage model singletons. This is GigaAM's own GPU serialization, independent of any gateway-level engine switching.
*   **6-step pipeline (sequential GPU, `flush_memory` between heavy stages):**
    1.  **ffmpeg (CPU):** any input → `audio_16k_mono.wav`. On non-zero exit, ffmpeg `stderr` is logged at `ERROR` and a clear `RuntimeError` (with the stderr tail) is raised — no silent `CalledProcessError`.
    2.  **DeepFilterNet3 (GPU, optional):** 16k→48k → denoise → 48k→16k → `audio_clean.wav`; flush VRAM.
    3.  **Pyannote (GPU, if `diarize=true`):** diarization on **original** `audio_16k_mono.wav`; flush VRAM. Segmentation batch size is configurable via `GIGAAM_DIARIZE_BATCH_SIZE` (default 8) to bound peak VRAM (RTX 3060 6 GB) — analogous to the denoise windowing knobs above.
    4.  **Silero VAD (CPU):** speech chunks ≤ 24.9 s on **clean** audio; Force Split at min RMS if needed.
    5.  **GigaAM ONNX ASR (GPU):** batch inference per chunk; flush VRAM.
    6.  **Wav2Vec2 alignment (GPU):** per-chunk forced alignment + hallucination filter (`score < 0.15` drop, `0.15–0.60` → `low_conf`); flush VRAM.
    7.  **IoW merge (CPU, if `diarize=true`):** word timestamps (clean) × speaker RTTM (original); backchannel tie-breaker assigns short overlaps to shorter RTTM segment.
*   **`diarize=false`:** steps 1 → 1b (opt.) → 4–6 only; `speaker` is `null` in segments.
*   **Endpoints:**
    *   `POST /v1/audio/transcriptions/local`: JSON `file_path`, `diarize`, `language`, `response_format`.
    *   `GET /health`: readiness (503 while bootstrapping).
    *   `POST /unload`: Frees all GPU models and clears CUDA cache.
