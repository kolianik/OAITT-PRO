import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

# Import the Gateway FastAPI app
import sys
import os
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
    
    # No auth header
    response = client.post("/v1/audio/transcriptions", files={"file": ("test.wav", b"dummy_bytes")})
    assert response.status_code == 401
    
    # Invalid token
    response = client.post(
        "/v1/audio/transcriptions", 
        files={"file": ("test.wav", b"dummy_bytes")},
        headers={"Authorization": "Bearer invalid_key"}
    )
    assert response.status_code == 401

@pytest.mark.asyncio
@patch("gateway.main.authenticate_client", new_callable=AsyncMock)
@patch("gateway.main.log_transcription", new_callable=AsyncMock)
@patch("httpx.AsyncClient.post", new_callable=AsyncMock)
async def test_vram_lock_and_unload_logic(mock_httpx_post, mock_log, mock_auth, client):
    """Verify that VRAM unloading commands are sent when switching models."""
    import gateway.main
    
    # Reset active model
    gateway.main.current_gpu_engine = "gigaam"
    
    # Mock auth success
    mock_auth.return_value = {"id": "test-uuid", "name": "Test Client", "role": "client"}
    
    # Mock successful inference response from WhisperX backend
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "Привет мир",
        "duration": 2.5,
        "language": "ru",
        "segments": [
            {"id": 0, "start": 0.0, "end": 2.5, "text": "Привет мир", "avg_logprob": -0.15}
        ]
    }
    
    # The first mock call is to GigaAM's /unload, second is to WhisperX's /v1/audio/transcriptions
    mock_unload_response = MagicMock()
    mock_unload_response.status_code = 200
    
    mock_httpx_post.side_effect = [mock_unload_response, mock_response]
    
    # Act: Request WhisperX (when GigaAM was hot)
    response = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("test.wav", b"dummy_bytes")},
        data={"model": "whisperx"},
        headers={"Authorization": "Bearer valid-token"}
    )
    
    # Assert
    assert response.status_code == 200
    assert response.json()["text"] == "Привет мир"
    
    # Verify GigaAM was commanded to unload because we requested WhisperX
    assert mock_httpx_post.call_count == 2
    # First call must be the unload endpoint for GigaAM
    first_call_args = mock_httpx_post.call_args_list[0]
    assert "http://gigaam-service:9007/unload" in first_call_args[0][0]
    
    # State should update to whisperx
    assert gateway.main.current_gpu_engine == "whisperx"

@pytest.mark.asyncio
@patch("gateway.main.authenticate_client", new_callable=AsyncMock)
@patch("gateway.main.log_transcription", new_callable=AsyncMock)
@patch("httpx.AsyncClient.post", new_callable=AsyncMock)
async def test_anti_hallucination_filters(mock_httpx_post, mock_log, mock_auth, client):
    """Verify that segments with low logprob are filtered out, text is reconstructed, and status is logged."""
    import gateway.main
    gateway.main.current_gpu_engine = "whisperx"
    
    mock_auth.return_value = {"id": "test-uuid", "name": "Test Client", "role": "client"}
    
    # Mock inference response containing one good and one bad segment
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "Хороший текст. Плохой галлюциногенный текст.",
        "duration": 5.0,
        "language": "ru",
        "segments": [
            {"id": 0, "start": 0.0, "end": 2.0, "text": "Хороший текст.", "avg_logprob": -0.1},
            {"id": 1, "start": 2.1, "end": 5.0, "text": "Плохой галлюциногенный текст.", "avg_logprob": -1.8}
        ]
    }
    mock_httpx_post.return_value = mock_response
    
    # Act: Request transcription with min_avg_logprob = -0.5
    response = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("test.wav", b"dummy_bytes")},
        data={"model": "whisperx", "min_avg_logprob": -0.5},
        headers={"Authorization": "Bearer valid-token"}
    )
    
    assert response.status_code == 200
    resp_data = response.json()
    
    # Assert bad segment was filtered out
    assert resp_data["text"] == "Хороший текст."
    assert len(resp_data["segments"]) == 1
    assert resp_data["segments"][0]["text"] == "Хороший текст."
    
    # Assert database logging captured the 'hallucination_filtered' status
    mock_log.assert_called_once()
    logged_status = mock_log.call_args[1]["status"]
    assert logged_status == "hallucination_filtered"
    
    # Logged text character count should match filtered text length
    logged_chars = mock_log.call_args[1]["char_count"]
    assert logged_chars == len("Хороший текст.")

@pytest.mark.asyncio
@patch("gateway.main.authenticate_client", new_callable=AsyncMock)
@patch("gateway.main.get_analytics_summary", new_callable=AsyncMock)
async def test_analytics_summary_permissions(mock_summary, mock_auth, client):
    """Verify that admins see global analytics, and clients only see their own metrics."""
    mock_summary.return_value = {"total_transcriptions": 10, "by_engine": {}}
    
    # Case 1: Client key passed
    mock_auth.return_value = {"id": "client-uuid", "name": "Standard Client", "role": "client"}
    client.get("/api/v1/analytics/summary", headers={"Authorization": "Bearer client-token"})
    # Query summary should be filtered with client_id
    mock_summary.assert_called_with(client_id="client-uuid")
    
    mock_summary.reset_mock()
    
    # Case 2: Admin key passed
    mock_auth.return_value = {"id": "admin-uuid", "name": "Global Admin", "role": "admin"}
    client.get("/api/v1/analytics/summary", headers={"Authorization": "Bearer admin-token"})
    # Query summary should NOT contain client_id (global view)
    mock_summary.assert_called_with()
