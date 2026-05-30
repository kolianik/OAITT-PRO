# Agents & Microservices Specification (agents.ms)

This file defines the system architecture, component roles, interface contracts, and communication protocols for the OAITT-PRO transcription system.

---

## 1. System Topology

OAITT-PRO is structured as a multi-agent system of 5 containerized services running on a single RTX 3060 (12GB VRAM) host:

```
[Client] ---> [Front Nginx Proxy] (HTTPS/SSL CF DNS-01)
                    │
                    ▼
         [API Orchestrator Gateway] <---> [PostgreSQL 18 Database]
          │                     │
          ▼                     ▼
  [WhisperX Service]     [GigaAM Service]
  (large-v3-russian)      (v3_e2e_rnnt)
```

---

## 2. Agent Definitions & Contracts

### 2.1. Front Nginx Proxy (Security & TLS Agent)
*   **Role:** Expose only port 443 to the public internet, routing requests to the API Orchestrator.
*   **SSL Management:** Certbot sidecar running the Cloudflare DNS-01 challenge.
*   **Config Contract:**
    *   Expose: `443/tcp`
    *   Target: `http://api-orchestrator:9000`
    *   SSL Cert Storage: Shared Docker volume `/etc/letsencrypt`

---

### 2.2. PostgreSQL 18 Database (State & Analytics Agent)
*   **Role:** Persist API clients, usage logs, and analytics.
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

#### Table: `transcription_logs`
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

---

### 2.3. API Orchestrator (Gateway Agent)
*   **Role:** Act as the single entry point. Handle client authentication, manage model VRAM exclusivity, handle request queues, filter hallucinations, and record stats.
*   **Concurrency & VRAM Exclusivity Rules:**
    1.  Maintains an internal `asyncio.Lock` to guarantee that only one GPU job executes at a time.
    2.  Tracks the currently active GPU service: `current_gpu_engine` (`whisperx` or `gigaam`).
    3.  If a request comes for `gigaam` and `current_gpu_engine == "whisperx"`, send `POST http://whisperx-service:9007/unload` to free memory. Then route the job to `gigaam-service`.
    4.  If a request comes for `whisperx` and `current_gpu_engine == "gigaam"`, send `POST http://gigaam-service:9007/unload` to free memory. Then route the job to `whisperx-service`.

*   **API Router Contracts:**
    *   `POST /v1/audio/transcriptions` (OpenAI format, supports form fields `file`, `model`, `language`, `diarize`, `min_avg_logprob`, `max_chars_per_second`).
    *   `POST /asr` (Standard / Custom format).
    *   `GET /api/v1/analytics/summary` (Analytics dashboard).
    *   `GET /health` (System monitoring).

---

### 2.4. WhisperX Service (WhisperX Agent)
*   **Role:** High-accuracy Whisper-based transcription + phoneme alignment + Pyannote v4 diarization.
*   **Model:** `bzikst/faster-whisper-large-v3-russian` in `float16` precision.
*   **Diarization Pipeline:** `pyannote/speaker-diarization-community-1` (requires Pyannote v4).
*   **Endpoints:**
    *   `POST /transcribe`: Takes audio file + configurations and performs transcription, alignment, and diarization.
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
    *   `POST /transcribe`: Takes audio file + configurations, performs diarization-based chunked GigaAM transcription.
    *   `POST /unload`: Unloads GigaAM and Pyannote models, executes garbage collection and `torch.cuda.empty_cache()`.
