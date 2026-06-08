# API Reference: Admin Client Pricing

**Version:** 1.1.0

Manage per-client billing tariffs. All endpoints require the global admin API key (`ADMIN_KEY`). Currency is fixed to `RUB`.

---

## POST /api/v1/admin/pricing

Create a new tariff row for a client. Closes any previously open tariff (`valid_to IS NULL`) by setting `valid_to = new_valid_from - 1 day`.

*   **Method:** `POST`
*   **Headers:** `Authorization: Bearer <ADMIN_KEY>`, `Content-Type: application/json`

### Request Body

| Field | Type | Required | Description |
|:---|:---|:---:|:---|
| `client_id` | string (UUID) | Yes | Target client. |
| `audio_price_per_minute` | float | No* | Rate per minute of total audio duration. |
| `speech_price_per_minute` | float | No* | Rate per minute of speech (VAD) duration. |
| `valid_from` | string | No | Start date `YYYY-MM-DD`. Default: server `CURRENT_DATE`. |

\* At least one of `audio_price_per_minute` or `speech_price_per_minute` must be provided.

### Responses

| Status | Description |
|:---|:---|
| `201` | Tariff created. Body: tariff record (see below). |
| `400` | Invalid `client_id`, invalid `valid_from`, or unknown client. |
| `401` | Invalid or missing token. |
| `403` | Non-admin token. |
| `409` | Duplicate `(client_id, currency, valid_from)`. |
| `422` | Body validation failed (e.g. both prices omitted). |

### Example

```bash
curl -X POST "https://${API_PUBLIC_HOST}/api/v1/admin/pricing" \
  -H "Authorization: Bearer <ADMIN_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "00000000-0000-0000-0000-000000000000",
    "audio_price_per_minute": 0.3,
    "speech_price_per_minute": 0.4,
    "valid_from": "2026-06-01"
  }'
```

### Response (`201 Created`)

```json
{
  "id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "client_id": "00000000-0000-0000-0000-000000000000",
  "audio_price_per_minute": 0.3,
  "speech_price_per_minute": 0.4,
  "currency": "RUB",
  "valid_from": "2026-06-01",
  "valid_to": null,
  "created_at": "2026-06-01T12:00:00+00:00"
}
```

---

## GET /api/v1/admin/pricing

Returns the currently active tariff for a client.

*   **Query:** `client_id` (UUID, required)

| Status | Description |
|:---|:---|
| `200` | Active tariff record. |
| `404` | No active tariff for this client. |

```bash
curl -X GET "https://${API_PUBLIC_HOST}/api/v1/admin/pricing?client_id=00000000-0000-0000-0000-000000000000" \
  -H "Authorization: Bearer <ADMIN_KEY>"
```

---

## GET /api/v1/admin/pricing/history

Returns all tariff rows for a client, newest `valid_from` first.

*   **Query:** `client_id` (UUID, required)

| Status | Description |
|:---|:---|
| `200` | JSON array of tariff records. |

```bash
curl -X GET "https://${API_PUBLIC_HOST}/api/v1/admin/pricing/history?client_id=00000000-0000-0000-0000-000000000000" \
  -H "Authorization: Bearer <ADMIN_KEY>"
```
