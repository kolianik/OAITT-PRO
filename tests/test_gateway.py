import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime, timezone

# Import the Gateway FastAPI app
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gateway")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gateway.main import app, current_gpu_engine

@pytest.fixture
def client():
    # Disable actual DB init for unit testing the routing layer
    with patch("gateway.main.init_db", new_callable=AsyncMock), \
         patch("gateway.main.close_db", new_callable=AsyncMock):
        with TestClient(app) as c:
            yield c

@pytest.mark.asyncio
@patch("gateway.main.authenticate_client", new_callable=AsyncMock)
async def test_auth_unauthorized(mock_auth, client):
    """Verify that requests without authorization or with invalid token are rejected with 401."""
    mock_auth.return_value = None
    
    # No auth header (POST)
    response = client.post("/v1/audio/transcriptions/async", files={"file": ("test.wav", b"dummy_bytes")})
    assert response.status_code == 401
    
    # No auth header (GET)
    response = client.get("/v1/audio/transcriptions/status/dummy-id")
    assert response.status_code == 401
    
    # Invalid token
    response = client.post(
        "/v1/audio/transcriptions/async", 
        files={"file": ("test.wav", b"dummy_bytes")},
        headers={"Authorization": "Bearer invalid_key"}
    )
    assert response.status_code == 401

@pytest.mark.asyncio
@patch("gateway.main.authenticate_client", new_callable=AsyncMock)
@patch("gateway.main.create_transcription_job", new_callable=AsyncMock)
async def test_async_job_creation(mock_create_job, mock_auth, client):
    """Verify that a POST to the async endpoint returns 202 Accepted and registers the task in DB."""
    mock_auth.return_value = {"id": "test-client-uuid", "name": "Test Client", "role": "client"}
    mock_create_job.return_value = "mocked-job-uuid-1234"
    
    # Mock os.makedirs and open to prevent actual disk writing during test
    with patch("os.makedirs"), patch("builtins.open", MagicMock()):
        response = client.post(
            "/v1/audio/transcriptions/async",
            files={"file": ("test.wav", b"fake audio content")},
            data={"model": "whisperx", "diarize": "true", "webhook_url": "http://callback.io"},
            headers={"Authorization": "Bearer valid-token"}
        )
        
    assert response.status_code == 202
    resp_json = response.json()
    assert resp_json["job_id"] == "mocked-job-uuid-1234"
    assert resp_json["status"] == "pending"
    assert "created_at" in resp_json
    
    # Verify DB insertion call arguments
    assert mock_create_job.call_count == 1
    call_args_dict = mock_create_job.call_args[1]
    assert call_args_dict["client_id"] == "test-client-uuid"
    assert call_args_dict["filename"] == "test.wav"
    assert call_args_dict["file_path"].startswith("/shared_data/")
    assert call_args_dict["file_path"].endswith(".wav")
    assert call_args_dict["engine"] == "whisperx"
    assert call_args_dict["model_name"] == "bzikst/faster-whisper-large-v3-russian"
    assert call_args_dict["diarization_enabled"] is True
    assert call_args_dict["language"] is None
    assert call_args_dict["response_format"] == "json"
    assert call_args_dict["min_avg_logprob"] is None
    assert call_args_dict["max_chars_per_second"] is None
    assert call_args_dict["webhook_url"] == "http://callback.io"

@pytest.mark.asyncio
@patch("gateway.main.authenticate_client", new_callable=AsyncMock)
@patch("gateway.main.get_transcription_job", new_callable=AsyncMock)
async def test_get_job_status_pending_processing(mock_get_job, mock_auth, client):
    """Verify that GET /status returns correct status when job is pending or processing."""
    mock_auth.return_value = {"id": "test-client-uuid", "name": "Test Client", "role": "client"}
    
    now = datetime.now(timezone.utc)
    mock_get_job.return_value = {
        "id": "mocked-job-uuid-1234",
        "client_id": "test-client-uuid",
        "status": "processing",
        "created_at": now,
        "updated_at": now,
        "filename": "test.wav",
        "file_path": "/shared_data/mocked-job-uuid-1234.wav",
        "engine": "whisperx",
        "model_name": "bzikst/faster-whisper-large-v3-russian",
        "diarization_enabled": False,
        "language": None,
        "response_format": "json",
        "min_avg_logprob": None,
        "max_chars_per_second": None,
        "webhook_url": None,
        "result": None,
        "error_message": None
    }
    
    response = client.get(
        "/v1/audio/transcriptions/status/mocked-job-uuid-1234",
        headers={"Authorization": "Bearer valid-token"}
    )
    
    assert response.status_code == 200
    resp_json = response.json()
    assert resp_json["job_id"] == "mocked-job-uuid-1234"
    assert resp_json["status"] == "processing"
    assert resp_json["created_at"] == now.isoformat()
    assert "result" not in resp_json

@pytest.mark.asyncio
@patch("gateway.main.authenticate_client", new_callable=AsyncMock)
@patch("gateway.main.get_transcription_job", new_callable=AsyncMock)
async def test_get_job_status_completed_formats(mock_get_job, mock_auth, client):
    """Verify that GET /status returns fully formatted result based on output parameter when completed."""
    mock_auth.return_value = {"id": "test-client-uuid", "name": "Test Client", "role": "client"}
    
    now = datetime.now(timezone.utc)
    mock_get_job.return_value = {
        "id": "mocked-job-uuid-1234",
        "client_id": "test-client-uuid",
        "status": "completed",
        "created_at": now,
        "updated_at": now,
        "filename": "test.wav",
        "file_path": "/shared_data/mocked-job-uuid-1234.wav",
        "engine": "whisperx",
        "model_name": "bzikst/faster-whisper-large-v3-russian",
        "diarization_enabled": True,
        "language": "ru",
        "response_format": "json",
        "min_avg_logprob": None,
        "max_chars_per_second": None,
        "webhook_url": None,
        "result": {
            "text": "Привет мир. Как дела?",
            "duration": 5.0,
            "segments": [
                {"id": 0, "start": 0.0, "end": 2.0, "text": "Привет мир.", "speaker": "SPEAKER_00"},
                {"id": 1, "start": 2.5, "end": 5.0, "text": "Как дела?", "speaker": "SPEAKER_01"}
            ]
        },
        "error_message": None
    }
    
    # 1. Test JSON output
    response = client.get(
        "/v1/audio/transcriptions/status/mocked-job-uuid-1234?output=json",
        headers={"Authorization": "Bearer valid-token"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["result"]["text"] == "Привет мир. Как дела?"
    
    # 2. Test Text output
    response = client.get(
        "/v1/audio/transcriptions/status/mocked-job-uuid-1234?output=text",
        headers={"Authorization": "Bearer valid-token"}
    )
    assert response.status_code == 200
    assert response.text == "Привет мир. Как дела?"
    
    # 3. Test SRT output
    response = client.get(
        "/v1/audio/transcriptions/status/mocked-job-uuid-1234?output=srt",
        headers={"Authorization": "Bearer valid-token"}
    )
    assert response.status_code == 200
    assert "[SPEAKER_00] Привет мир." in response.text
    assert "00:00:02,500 --> 00:00:05,000" in response.text

@pytest.mark.asyncio
@patch("gateway.main.authenticate_client", new_callable=AsyncMock)
@patch("gateway.main.get_transcription_job", new_callable=AsyncMock)
async def test_get_job_status_permissions(mock_get_job, mock_auth, client):
    """Verify security boundaries: standard clients cannot view other clients' jobs, but admins can."""
    now = datetime.now(timezone.utc)
    mock_get_job.return_value = {
        "id": "mocked-job-uuid-1234",
        "client_id": "other-client-uuid", # Belongs to another client!
        "status": "completed",
        "created_at": now,
        "updated_at": now,
        "filename": "test.wav",
        "file_path": "/shared_data/mocked-job-uuid-1234.wav",
        "engine": "whisperx",
        "model_name": "bzikst/faster-whisper-large-v3-russian",
        "diarization_enabled": False,
        "language": None,
        "response_format": "json",
        "min_avg_logprob": None,
        "max_chars_per_second": None,
        "webhook_url": None,
        "result": {"text": "Hello"},
        "error_message": None
    }
    
    # Standard client requests: should be 403 Forbidden
    mock_auth.return_value = {"id": "my-client-uuid", "name": "Standard Client", "role": "client"}
    response = client.get(
        "/v1/audio/transcriptions/status/mocked-job-uuid-1234",
        headers={"Authorization": "Bearer client-token"}
    )
    assert response.status_code == 403
    
    # Admin requests: should be 200 OK
    mock_auth.return_value = {"id": "admin-uuid", "name": "Global Admin", "role": "admin"}
    response = client.get(
        "/v1/audio/transcriptions/status/mocked-job-uuid-1234",
        headers={"Authorization": "Bearer admin-token"}
    )
    assert response.status_code == 200

@pytest.mark.asyncio
@patch("gateway.main.authenticate_client", new_callable=AsyncMock)
@patch("gateway.main.get_analytics_summary", new_callable=AsyncMock)
async def test_analytics_summary_permissions(mock_summary, mock_auth, client):
    """Verify that admins see global analytics, and clients only see their own metrics."""
    mock_summary.return_value = {"total_transcriptions": 10, "by_engine": {}}
    
    # Case 1: Client key passed
    mock_auth.return_value = {"id": "client-uuid", "name": "Standard Client", "role": "client"}
    client.get("/api/v1/analytics/summary", headers={"Authorization": "Bearer client-token"})
    mock_summary.assert_called_with(
        client_id="client-uuid", date_from=None, date_to=None
    )
    
    mock_summary.reset_mock()
    
    # Case 2: Admin key passed
    mock_auth.return_value = {"id": "admin-uuid", "name": "Global Admin", "role": "admin"}
    client.get("/api/v1/analytics/summary", headers={"Authorization": "Bearer admin-token"})
    mock_summary.assert_called_with(client_id=None, date_from=None, date_to=None)


@pytest.mark.asyncio
@patch("gateway.main.authenticate_client", new_callable=AsyncMock)
@patch("gateway.main.get_analytics_summary", new_callable=AsyncMock)
async def test_analytics_summary_date_params(mock_summary, mock_auth, client):
    mock_summary.return_value = {"total_transcriptions": 0, "cost": {}}
    mock_auth.return_value = {"id": "client-uuid", "name": "Standard Client", "role": "client"}
    client.get(
        "/api/v1/analytics/summary?from=2026-05-01&to=2026-05-31",
        headers={"Authorization": "Bearer client-token"},
    )
    mock_summary.assert_called_with(
        client_id="client-uuid", date_from="2026-05-01", date_to="2026-05-31"
    )


@pytest.mark.asyncio
@patch("gateway.main.authenticate_client", new_callable=AsyncMock)
async def test_analytics_client_id_forbidden(mock_auth, client):
    mock_auth.return_value = {"id": "client-uuid", "name": "Standard Client", "role": "client"}
    response = client.get(
        "/api/v1/analytics/summary?client_id=00000000-0000-0000-0000-000000000000",
        headers={"Authorization": "Bearer client-token"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
@patch("gateway.main.authenticate_client", new_callable=AsyncMock)
@patch("gateway.main.get_analytics_summary", new_callable=AsyncMock)
async def test_analytics_admin_client_id_filter(mock_summary, mock_auth, client):
    mock_summary.return_value = {"total_transcriptions": 0}
    mock_auth.return_value = {"id": "admin-uuid", "name": "Global Admin", "role": "admin"}
    cid = "00000000-0000-0000-0000-000000000000"
    client.get(
        f"/api/v1/analytics/summary?client_id={cid}",
        headers={"Authorization": "Bearer admin-token"},
    )
    mock_summary.assert_called_with(client_id=cid, date_from=None, date_to=None)


@pytest.mark.asyncio
@patch("gateway.main.authenticate_client", new_callable=AsyncMock)
@patch("gateway.main.get_active_client_pricing", new_callable=AsyncMock)
async def test_admin_pricing_forbidden_for_client(mock_pricing, mock_auth, client):
    mock_auth.return_value = {"id": "client-uuid", "name": "Standard Client", "role": "client"}
    response = client.get(
        "/api/v1/admin/pricing?client_id=00000000-0000-0000-0000-000000000000",
        headers={"Authorization": "Bearer client-token"},
    )
    assert response.status_code == 403
    mock_pricing.assert_not_called()


@pytest.mark.asyncio
@patch("gateway.main.authenticate_client", new_callable=AsyncMock)
@patch("gateway.main.get_analytics_by_client", new_callable=AsyncMock)
async def test_admin_by_client_requires_admin(mock_by_client, mock_auth, client):
    mock_auth.return_value = {"id": "client-uuid", "name": "Standard Client", "role": "client"}
    response = client.get(
        "/api/v1/admin/analytics/by-client?from=2026-05-01&to=2026-06-01",
        headers={"Authorization": "Bearer client-token"},
    )
    assert response.status_code == 403
    mock_by_client.assert_not_called()
