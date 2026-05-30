# API Reference: POST /v1/audio/transcriptions

The primary transcription endpoint. It is fully compatible with OpenAI's Audio Transcriptions API but extended with advanced features like Pyannote v4 speaker diarization, anti-hallucination thresholds, and dynamic GigaAM VRAM selection.

---

## 📡 Protocol Specification

*   **URL:** `/v1/audio/transcriptions`
*   **Method:** `POST`
*   **Content-Type:** `multipart/form-data`
*   **Headers:**
    *   `Authorization`: `Bearer <API_TOKEN>` (**Required**)

---

## 📋 Multipart Form-Data Parameters

| Parameter | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| **file** | `file` (binary) | **Yes** | — | The audio file to transcribe (wav, mp3, flac, m4a, etc.). |
| **model** | `string` | No | `whisper-1` | Which model engine to route to. Use `gigaam` for GigaAM RNNT and `whisper-1` or `whisperx` for WhisperX Large V3. |
| **language** | `string` | No | — | ISO-639-1 language code (e.g., `ru`, `en`). |
| **response_format** | `string` | No | `json` | Return format: `json` or `verbose_json`. |
| **diarize** | `boolean` | No | `false` | Enable **Pyannote.audio v4.0.4** speaker diarization (works with both engines). |
| **min_avg_logprob** | `float` | No | — | Anti-hallucination threshold. Discards segments with logprob lower than this value. |
| **max_chars_per_second** | `float` | No | — | Anti-hallucination speech rate limit. Discards segments with characters/sec higher than this value. |

---

## 💻 Request & Response Examples

### 1. Simple WhisperX Transcription (JSON)
```bash
curl -X POST "https://api.yourdomain.com/v1/audio/transcriptions" \
     -H "Authorization: Bearer default-client-key" \
     -F "file=@/path/to/audio.mp3" \
     -F "model=whisper-1"
```
#### Response (`200 OK`):
```json
{
  "text": "Здравствуйте, это тестовая запись."
}
```

### 2. GigaAM Transcription with Diarization and Filters (Verbose JSON)
```bash
curl -X POST "https://api.yourdomain.com/v1/audio/transcriptions" \
     -H "Authorization: Bearer default-client-key" \
     -F "file=@/path/to/long_audio.wav" \
     -F "model=gigaam" \
     -F "response_format=verbose_json" \
     -F "diarize=true" \
     -F "min_avg_logprob=-0.5" \
     -F "max_chars_per_second=25.0"
```
#### Response (`200 OK`):
```json
{
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
      "avg_logprob": 0.0,
      "chars_per_second": 5.23
    },
    {
      "id": 1,
      "start": 4.5,
      "end": 12.5,
      "text": "Всё отлично, спасибо!",
      "speaker": "SPEAKER_01",
      "avg_logprob": 0.0,
      "chars_per_second": 2.62
    }
  ]
}
```
*Note: GigaAM RNNT uses custom Pyannote v4 voice chunking and speaker overlapping mapped into the response segments.*
