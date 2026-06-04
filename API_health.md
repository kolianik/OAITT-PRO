# API Reference: GET /health

Returns the real-time operational status of the gateway, connection state to PostgreSQL 17, currently hot model in VRAM, and online statuses of the downstream inference services.

---

## 📡 Protocol Specification

*   **URL:** `/health`
*   **Method:** `GET`
*   **Authentication:** Not required (allows seamless monitoring by load balancers, Kubernetes liveness probes, and Prometheus).

---

## 📋 Response Payload Structure

The endpoint returns a JSON object with the following fields:

*   **`status`** (*string*): The overall status of the system. Can be:
    *   `healthy`: Fully operational (Postgres is reachable, and at least one model service is online).
    *   `unhealthy`: Not operational (Postgres is down, or both backend model services are offline).
*   **`database_connected`** (*boolean*): Connection status to PostgreSQL 17.
*   **`currently_hot_model`** (*string*): The model currently kept hot in GPU memory. Can be `"whisperx"`, `"gigaam"`, or `"none"`.
*   **`whisperx_service`** (*string*): The state of the WhisperX backend. Can be `"online"` or `"offline"`.
*   **`gigaam_service`** (*string*): The state of the GigaAM backend. Can be `"online"` or `"offline"`.

---

## 💻 Request & Response Examples

### Example Request (cURL)
```bash
curl -X GET "https://api.yourdomain.com/health" -H "Accept: application/json"
```

### Example Successful Response (`200 OK`)
```json
{
  "status": "healthy",
  "database_connected": true,
  "currently_hot_model": "whisperx",
  "whisperx_service": "online",
  "gigaam_service": "online"
}
```
