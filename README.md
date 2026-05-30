# OAITT-PRO — Open AI Transformer Transcriber PRO

Highly optimized speech-to-text service with advanced speaker diarization and dynamic VRAM management, designed specifically for NVIDIA RTX 3060 (12GB VRAM).

---

## 🚀 Key Features

*   **Dual ASR Engines:** 
    *   **WhisperX:** Utilizing highly-optimized `bzikst/faster-whisper-large-v3-russian` in FP16.
    *   **GigaAM:** Utilizing `v3_e2e_rnnt` with integrated punctuation.
*   **State-of-the-Art Diarization:** Powered by **Pyannote.audio v4.0.4** (`pyannote/speaker-diarization-community-1`).
*   **Dynamic VRAM Management:** Automatically unloads unused models to allow sharing a single GPU without out-of-memory errors.
*   **Multi-Client Support:** Built-in PostgreSQL-backed API key management and per-client analytics.
*   **Anti-Hallucination Filters:** Restrict low-confidence transcriptions based on `avg_logprob` and `chars_per_second` thresholds.

---

## 📡 API Reference

All requests must include the `Authorization` header:
```http
Authorization: Bearer <your_api_key>
```

### 1. OpenAI-Compatible Transcription
Provides a drop-in replacement for OpenAI's Whisper API.

*   **URL:** `/v1/audio/transcriptions`
*   **Method:** `POST`
*   **Content-Type:** `multipart/form-data`
*   **Parameters:**
    *   `file` (file, required): The audio file.
    *   `model` (string, default: `whisper-1`): Engine to use. Use `whisper-1` or `whisperx` for WhisperX, and `gigaam` for GigaAM.
    *   `language` (string, optional): ISO language code (e.g. `ru`, `en`).
    *   `response_format` (string, default: `json`): `json` or `verbose_json`.
    *   **`diarize` (boolean, default: `false`):** Enable Pyannote v4 speaker diarization.
    *   **`min_avg_logprob` (float, optional):** Filter out segments with log probability lower than this threshold.
    *   **`max_chars_per_second` (float, optional):** Mark transcription as unreliable or filter if speech generation rate exceeds this value (anti-hallucination filter).

#### Response Example (`response_format=verbose_json` with `diarize=true`):
```json
{
  "text": "Привет! Меня зовут Антон. А меня зовут Сергей.",
  "task": "transcribe",
  "language": "ru",
  "duration": 5.4,
  "segments": [
    {
      "id": 0,
      "start": 0.0,
      "end": 2.1,
      "text": "Привет! Меня зовут Антон.",
      "speaker": "SPEAKER_00",
      "avg_logprob": -0.15,
      "chars_per_second": 11.9
    },
    {
      "id": 1,
      "start": 2.5,
      "end": 5.4,
      "text": "А меня зовут Сергей.",
      "speaker": "SPEAKER_01",
      "avg_logprob": -0.22,
      "chars_per_second": 6.8
    }
  ]
}
```

---

### 2. Flexible ASR Endpoint
Provides native SRT, VTT, TSV, text, and JSON formatting.

*   **URL:** `/asr`
*   **Method:** `POST`
*   **Content-Type:** `multipart/form-data`
*   **Parameters:**
    *   `audio_file` (file, required): The audio file.
    *   `output` (string, default: `json`): `json`, `text`, `srt`, `vtt`, `tsv`.
    *   `diarize` (boolean, default: `false`): Enable diarization.
    *   `min_avg_logprob` (float, optional)
    *   `max_chars_per_second` (float, optional)

---

### 3. Analytics Dashboard
Retrieve usage metrics. Admin keys can view overall stats, while client keys only retrieve their own metrics.

*   **URL:** `/api/v1/analytics/summary`
*   **Method:** `GET`
*   **Response:**
```json
{
  "total_transcriptions": 125,
  "total_audio_duration_seconds": 3600.0,
  "total_speech_duration_seconds": 3240.5,
  "total_processing_seconds": 240.2,
  "average_rtf": 0.067,
  "success_rate": 0.984,
  "by_engine": {
    "whisperx": 75,
    "gigaam": 50
  }
}
```

---

### 4. Health Check
*   **URL:** `/health`
*   **Method:** `GET`
*   **Response:**
```json
{
  "status": "healthy",
  "database_connected": true,
  "currently_hot_model": "whisperx",
  "whisperx_service": "online",
  "gigaam_service": "online"
}
```

---

## 🛠️ Configuration & Secrets

The system is configured via environment variables in the `.env` file:

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_DB` | `transcribe_db` | Postgres Database Name |
| `POSTGRES_USER` | `postgres` | Postgres Username |
| `POSTGRES_PASSWORD` | `secure_pass` | Postgres Password |
| `HF_TOKEN` | *required* | Hugging Face Access Token |
| `ADMIN_KEY` | *required* | Global administrative API Key |
| `PROXY_PORT_HTTPS` | `443` | Custom HTTPS port exposed by the proxy on the host |
| `PROXY_PORT_HTTP` | `80` | Custom HTTP port exposed by the proxy on the host |
| `GATEWAY_PORT` | `9000` | Custom port exposed by the API Orchestrator on the host |
| `WHISPERX_MODEL` | `bzikst/faster-whisper-large-v3-russian` | Hugging Face model path for WhisperX |
| `GIGAAM_MODEL` | `v3_e2e_rnnt` | Model name for GigaAM service |
| `DEVICE` | `cuda` | Hardware accelerator device (cuda/cpu) |
| `WHISPERX_COMPUTE_TYPE` | `float16` | Quantization precision for WhisperX |

---

## 🌐 NAT & Single-Port Deployment (Cloudflare CDN)

If you are running the server behind a NAT with only one open port, you can leverage Cloudflare's **Origin Rules** to route standard HTTPS traffic (port 443) to your custom open port. 

For detailed step-by-step instructions on configuring Cloudflare and setting up `.env` for NAT, please refer to the **[NAT & Cloudflare Guide in TROUBLESHOOTING.md](TROUBLESHOOTING.md#3-nat--single-port-deployment-via-cloudflare)**.

---

## 💖 Inspiration & Credits

This project was inspired by and built upon the excellent work of the **[oaitt](https://github.com/haiodo/oaitt)** repository. Special thanks to the original creators for laying down the foundation for high-performance speech transcription and orchestration!
