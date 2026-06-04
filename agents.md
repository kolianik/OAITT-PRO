# Agents & Microservices Specification (agents.md)

This file defines the system architecture, component roles, interface contracts, and communication protocols for the OAITT-PRO transcription system.

---

## 1. System Topology

OAITT-PRO is structured as a multi-agent system of 5 containerized services running on a single RTX 3060 (12GB VRAM) host. 
To bypass Cloudflare body size (100MB) limits, uploads are directed to port `3000` on the firewall (unproxied host IP), while status queries, analytics, and health-checks go through the proxied port `443`:

```
[Client] ──(Upload to :3000)──► [Front Nginx Proxy] (Port 80)
[Client] ──(Query to :443)───► [Cloudflare Proxy] ──► [Front Nginx Proxy] (Port 80)
                                      │
                                      ▼
                           [API Orchestrator Gateway] <───► [PostgreSQL 17]
                                  │       │
             (Shared Volume)      │       │     (Shared Volume)
             `/shared_data/` ◄────┘       └────► `/shared_data/`
                    │                                   │
                    ▼                                   ▼
            [WhisperX Service]                  [GigaAM Service]
           (large-v3-russian)                    (v3_e2e_rnnt)
```

---

## 2. Agent Definitions & Contracts

### 2.1. Front Nginx Proxy (Security & TLS Agent)
*   **Role:** Exposes port 80 internally (forwarded from port 3000 at the firewall, or port 443 via Cloudflare CDN).
*   **SSL Management:** Certbot sidecar running the Cloudflare DNS-01 challenge.
*   **Config Contract:**
    *   Expose: `80/tcp`, `443/tcp` (NAT `3000` to internal `80`)
    *   Target: `http://gateway-orchestrator:9000`
    *   SSL Cert Storage: Shared Docker volume `/etc/letsencrypt`
    *   `client_max_body_size`: `2048M` (Supports large media files uploaded over unproxied port 3000)

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
*   **Model:** `bzikst/faster-whisper-large-v3-russian` in `float16` precision.
*   **Diarization Pipeline:** `pyannote/speaker-diarization-community-1` (requires Pyannote v4).
*   **Endpoints:**
    *   `POST /v1/audio/transcriptions/local`: Takes a JSON payload with `file_path`, loads the audio from the shared volume, and performs transcription, alignment, and diarization.
    *   `POST /unload`: Deletes the pipeline, forces PyTorch garbage collection, and runs `torch.cuda.empty_cache()` to free 100% of VRAM.

---

### 2.5. GigaAM Service (GigaAM Agent)
*   **Role:** Ultra-fast Russian speech transcription + custom Pyannote v4 diarization and VAD chunking.
*   **Model:** `v3_e2e_rnnt` (SberDevices RNNT with punctuation).
*   **VAD & Chunking Algorithm (Overcoming the 25-second limit):**
    For audio files longer than 25 seconds, GigaAM Service performs the following sequence:
    1.  Runs `pyannote/speaker-diarization-community-1` on the audio to generate speaker segments.
    2.  Collects speech intervals: `[{"start": t_start, "end": t_end, "speaker": spk}, ...]`.
    3.  For each interval, crops the audio and executes `gigaam.transcribe()` (guaranteeing highly accurate short tensor transcriptions, since each slice is < 25s).
    4.  Saves speech duration as `sum(t_end - t_start)`.
    5.  Assembles the segment transcriptions back into a unified `TranscriptionResponse`, assigning the corresponding `speaker` label directly to each text segment based on the Pyannote interval.
*   **Endpoints:**
    *   `POST /v1/audio/transcriptions/local`: Takes a JSON payload with `file_path`, loads/chunks the audio from the shared volume, and performs GigaAM transcription and speaker assignment.
    *   `POST /unload`: Unloads GigaAM and Pyannote models, executes garbage collection and `torch.cuda.empty_cache()`.
