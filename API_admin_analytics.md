# API Reference: GET /api/v1/admin/analytics/by-client

**Version:** 1.1.0

Admin-only report: analytics summary and `cost` per client for a mandatory date range. Clients with no logs in the period are omitted.

---

## Protocol Specification

*   **URL:** `/api/v1/admin/analytics/by-client`
*   **Method:** `GET`
*   **Headers:** `Authorization: Bearer <ADMIN_KEY>` (**Required**)

### Query Parameters

| Parameter | Required | Description |
|:---|:---:|:---|
| `from` | Yes | Start date inclusive (`YYYY-MM-DD`, UTC midnight). |
| `to` | Yes | End date inclusive (`YYYY-MM-DD`). |

Same date semantics as [`API_analytics.md`](API_analytics.md).

### Error Responses

| Status | Condition |
|:---|:---|
| `400` | Missing `from`/`to`, invalid date format, or `from > to`. |
| `401` | Invalid token. |
| `403` | Non-admin token. |

---

## Response

JSON **array** of objects, one per client with activity in the period:

| Field | Type | Description |
|:---|:---|:---|
| `client_id` | string (UUID) | Client identifier. |
| `client_name` | string | Client display name. |
| `total_transcriptions` | integer | Same as analytics summary. |
| `total_audio_duration_seconds` | float | |
| `total_speech_duration_seconds` | float | |
| `total_processing_seconds` | float | |
| `average_rtf` | float | |
| `success_rate` | float | |
| `by_engine` | object | |
| `cost` | object | Same structure as analytics summary `cost`. |

---

## Example

```bash
curl -X GET "https://${API_PUBLIC_HOST}/api/v1/admin/analytics/by-client?from=2026-05-01&to=2026-06-01" \
  -H "Authorization: Bearer <ADMIN_KEY>" \
  -H "Accept: application/json"
```

### Response (`200 OK`)

```json
[
  {
    "client_id": "00000000-0000-0000-0000-000000000000",
    "client_name": "Default Client",
    "total_transcriptions": 47,
    "total_audio_duration_seconds": 12210.0,
    "total_speech_duration_seconds": 9138.0,
    "total_processing_seconds": 488.2,
    "average_rtf": 0.04,
    "success_rate": 0.95,
    "by_engine": { "whisperx": 40, "gigaam": 7 },
    "cost": {
      "currency": "RUB",
      "audio_minutes": 203.5,
      "speech_minutes": 152.3,
      "pricing_options": {
        "by_audio_duration": { "price_per_minute": 0.3, "total": 61.05 },
        "by_speech_duration": { "price_per_minute": 0.4, "total": 60.92 }
      }
    }
  }
]
```
