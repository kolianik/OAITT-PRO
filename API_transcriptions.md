# API Reference: Asynchronous Transcription API

Due to Cloudflare's 100MB body size limit and 100-second request timeout limit on proxied connections, the OAITT-PRO transcription system employs an entirely asynchronous architecture. 

*   **Submitting Jobs:** Use the upload hostname and HTTP port from `.env`: `https://${API_UPLOAD_HOST}:${PROXY_PORT_HTTP}/v1/audio/transcriptions/async` (large files >200MB; DNS-only upload host bypasses CDN limits).
*   **Checking Status / Getting Results:** `https://${API_PUBLIC_HOST}/v1/audio/transcriptions/status/{job_id}`.

---

## 1. 📡 Submit Transcription Job

*   **URL:** `https://${API_UPLOAD_HOST}:${PROXY_PORT_HTTP}/v1/audio/transcriptions/async` (DNS-only upload host bypasses Cloudflare body limits)
*   **Method:** `POST`
*   **Content-Type:** `multipart/form-data`
*   **Headers:**
    *   `Authorization`: `Bearer <API_TOKEN>` (**Required**)

### 📋 Multipart Form-Data Parameters

| Parameter | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| **file** | `file` (binary) | **Yes** | — | The audio file to transcribe (wav, mp3, flac, m4a, etc.). |
| **model** | `string` | No | `whisper-1` | Which model engine to route to. Use `gigaam` for GigaAM RNNT and `whisper-1` or `whisperx` for WhisperX Large V3. |
| **language** | `string` | No | — | ISO-639-1 language code (e.g., `ru`, `en`). |
| **response_format** | `string` | No | `json` | Return format: `json` or `verbose_json`. |
| **diarize** | `boolean` | No | `false` | Enable **Pyannote.audio v4.0.4** speaker diarization (works with both engines). |
| **min_avg_logprob** | `float` | No | — | Anti-hallucination threshold. Discards segments with logprob lower than this value. |
| **max_chars_per_second** | `float` | No | — | Anti-hallucination speech rate limit. Discards segments with characters/sec higher than this value. |
| **webhook_url** | `string` | No | — | Optional **HTTPS** webhook URL (public hostname only). The gateway POSTs the result JSON when the job completes. Private IPs, localhost, link-local, and cloud metadata addresses are rejected. Redirects are not followed. |

### 💻 Request & Response Example

#### Request:
```bash
curl -X POST "https://${API_UPLOAD_HOST}:${PROXY_PORT_HTTP}/v1/audio/transcriptions/async" \
     -H "Authorization: Bearer <your_api_key>" \
     -F "file=@/path/to/large_audio.mp3" \
     -F "model=whisper-1" \
     -F "diarize=true" \
     -F "webhook_url=https://my-client-app.com/webhooks/transcribe"
```

#### Response (`202 Accepted`):
```json
{
  "job_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "status": "pending",
  "created_at": "2026-06-01T12:00:00.000000Z"
}
```

---

## 2. 📡 Get Job Status & Results

*   **URL:** `https://${API_PUBLIC_HOST}/v1/audio/transcriptions/status/{job_id}` (typically via CDN on port 443)
*   **Method:** `GET`
*   **Headers:**
    *   `Authorization`: `Bearer <API_TOKEN>` (**Required**)

### 📋 Query Parameters

| Parameter | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| **output** | `string` | No | `json` | Formats the returned transcription. Options: `json`, `text`, `srt`, `vtt`, `tsv`. |

### 💻 Response Examples

#### Case A: Job is still processing (`200 OK`):
```json
{
  "job_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "status": "processing",
  "created_at": "2026-06-01T12:00:00.000000+00:00",
  "updated_at": "2026-06-01T12:00:15.123456+00:00"
}
```

#### Case B: Job finished successfully (with `output=json`, `200 OK`):
```json
{
  "job_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "status": "completed",
  "created_at": "2026-06-01T12:00:00.000000+00:00",
  "updated_at": "2026-06-01T12:05:30.456789+00:00",
  "result": {
    "text": "Привет! Как твои дела? Всё отлично, спасибо!",
    "language": "ru",
    "duration": 12.5,
    "segments": [
      {
        "id": 0,
        "start": 0.0,
        "end": 4.2,
        "text": "Привет! Как твои дела?",
        "speaker": "SPEAKER_00",
        "avg_logprob": -0.12
      },
      {
        "id": 1,
        "start": 4.5,
        "end": 12.5,
        "text": "Всё отлично, спасибо!",
        "speaker": "SPEAKER_01",
        "avg_logprob": -0.05
      }
    ]
  }
}
```

#### Case C: Job failed (`200 OK`):
```json
{
  "job_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "status": "failed",
  "created_at": "2026-06-01T12:00:00.000000+00:00",
  "updated_at": "2026-06-01T12:01:45.987654+00:00",
  "error_message": "Inference engine returned status 500: CUDA Out of Memory"
}
```

---

## 3. 🛡️ Data Retention Policy

To manage free disk space on the RTX 3060 server efficiently and protect privacy:
1.  **Strict Immediate Deletion:** The source audio file uploaded by the client is stored in the shared workspace `/shared_data` and is **guaranteed to be deleted** immediately when the transcription job completes (whether successfully or in failure).
2.  **24-Hour Job Expiration:** Complete transcription records (status and text result) are kept in the PostgreSQL database for a maximum of **24 hours** after creation.
3.  **Automatic Garbage Collector:** A background garbage collector runs every hour to delete database records and any uncompleted/orphaned files older than 24 hours.
