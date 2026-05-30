# API Reference: GET /api/v1/analytics/summary

Retrieves historical transcription statistics and performance metrics recorded in PostgreSQL 18. Admin tokens have access to view global statistics for all clients, while regular client keys only retrieve metrics linked to their own API keys.

---

## 📡 Protocol Specification

*   **URL:** `/api/v1/analytics/summary`
*   **Method:** `GET`
*   **Headers:**
    *   `Authorization`: `Bearer <API_TOKEN>` (**Required**)

---

## 📋 Response Payload Structure

The endpoint returns a JSON object with the following fields:

*   **`total_transcriptions`** (*integer*): Total number of successfully completed transcription jobs.
*   **`total_audio_duration_seconds`** (*float*): Cumulative duration of all processed audio files in seconds.
*   **`total_speech_duration_seconds`** (*float*): Cumulative duration of speech segments in seconds (pure voice time, excluding silent parts detected by VAD).
*   **`total_processing_seconds`** (*float*): Cumulative wall-clock processing time spent on GPU inference.
*   **`average_rtf`** (*float*): Real-time factor (total processing time / total audio duration). Lower numbers signify faster performance (e.g. `0.067` means speech is processed at ~15x realtime speed).
*   **`success_rate`** (*float*): Percentage of successful jobs (ranges from `0.0` to `1.0`).
*   **`by_engine`** (*object*): Group count of successful jobs run per ASR engine. Format: `{"whisperx": integer, "gigaam": integer}`.

---

## 💻 Request & Response Examples

### Example Request (cURL)
```bash
curl -X GET "https://api.yourdomain.com/api/v1/analytics/summary" \
     -H "Authorization: Bearer default-client-key" \
     -H "Accept: application/json"
```

### Example Response (`200 OK`)
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
