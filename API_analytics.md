# API Reference: GET /api/v1/analytics/summary

**Version:** 1.1.0

Retrieves historical transcription statistics, performance metrics, and billing cost estimates recorded in PostgreSQL 17. Admin tokens can view global statistics (optionally filtered by client and date range). Regular client keys only retrieve metrics and cost for their own API key.

---

## Protocol Specification

*   **URL:** `/api/v1/analytics/summary`
*   **Method:** `GET`
*   **Headers:**
    *   `Authorization`: `Bearer <API_TOKEN>` (**Required**)

### Query Parameters

| Parameter | Required | Access | Description |
|:---|:---:|:---|:---|
| `from` | No | All | Start date inclusive, format `YYYY-MM-DD`. Filter: `created_at >= from 00:00:00 UTC`. |
| `to` | No | All | End date inclusive, format `YYYY-MM-DD`. Filter: `created_at < (to + 1 day) 00:00:00 UTC`. |
| `client_id` | No | Admin only | UUID of a specific client. Non-admin tokens receive `403` if this parameter is set. |

### Error Responses

| Status | Condition |
|:---|:---|
| `400` | Invalid date format, `from > to`, or invalid `client_id` (not a UUID). |
| `401` | Missing or invalid API token. |
| `403` | Non-admin token used `client_id` query parameter. |

---

## Response Payload Structure

*   **`total_transcriptions`** (*integer*): Total number of transcription log entries in the filtered set.
*   **`total_audio_duration_seconds`** (*float*): Cumulative audio duration in seconds.
*   **`total_speech_duration_seconds`** (*float*): Cumulative speech duration in seconds (VAD-based voice time).
*   **`total_processing_seconds`** (*float*): Cumulative GPU processing time in seconds.
*   **`average_rtf`** (*float*): Real-time factor (`total_processing_seconds / total_audio_duration_seconds`).
*   **`success_rate`** (*float*): Share of successful jobs (`0.0`–`1.0`).
*   **`by_engine`** (*object*): Job counts per engine, e.g. `{"whisperx": 75, "gigaam": 50}`.
*   **`cost`** (*object*): Billing estimate in RUB (always present; zero totals when no tariff applies).

### `cost` Object

| Field | Type | Description |
|:---|:---|:---|
| `currency` | string | Always `"RUB"`. |
| `audio_minutes` | float | Sum of audio minutes in the filtered set (2 decimal places). |
| `speech_minutes` | float | Sum of speech minutes (2 decimal places). |
| `pricing_options` | object | Present keys depend on configured tariff rates (see below). |

#### `pricing_options` Keys

Only included when the applicable tariff has a non-null rate for that dimension:

*   **`by_audio_duration`**: `{ "price_per_minute": float, "total": float }` — `total` is the sum of per-log `(audio_seconds / 60) * rate` using the tariff valid on each log's date. `price_per_minute` is included only when exactly one distinct audio rate applies across all logs in the period; otherwise only `total` is returned.
*   **`by_speech_duration`**: Same structure for speech minutes and speech rate.

When no tariff exists for a client/period, `pricing_options` is `{}` and totals are `0`.

---

## Request & Response Examples

### Example Request (client, date range)

```bash
curl -X GET "https://api.yourdomain.com/api/v1/analytics/summary?from=2026-05-01&to=2026-05-31" \
     -H "Authorization: Bearer <your_api_key>" \
     -H "Accept: application/json"
```

### Example Request (admin, specific client)

```bash
curl -X GET "https://api.yourdomain.com/api/v1/analytics/summary?from=2026-05-01&to=2026-06-01&client_id=00000000-0000-0000-0000-000000000000" \
     -H "Authorization: Bearer <ADMIN_KEY>" \
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
  },
  "cost": {
    "currency": "RUB",
    "audio_minutes": 60.0,
    "speech_minutes": 54.01,
    "pricing_options": {
      "by_audio_duration": {
        "price_per_minute": 0.3,
        "total": 18.0
      },
      "by_speech_duration": {
        "price_per_minute": 0.4,
        "total": 21.6
      }
    }
  }
}
```

### Example Response (no tariff)

```json
{
  "total_transcriptions": 10,
  "total_audio_duration_seconds": 600.0,
  "total_speech_duration_seconds": 500.0,
  "total_processing_seconds": 40.0,
  "average_rtf": 0.067,
  "success_rate": 1.0,
  "by_engine": { "whisperx": 10 },
  "cost": {
    "currency": "RUB",
    "audio_minutes": 10.0,
    "speech_minutes": 8.33,
    "pricing_options": {}
  }
}
```
