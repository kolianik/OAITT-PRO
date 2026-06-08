import os
import socket
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shared.security import (
    normalize_audio_extension,
    resolve_shared_data_path,
    validate_shared_path,
    validate_webhook_url,
)


@patch(
    "shared.security.socket.getaddrinfo",
    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
)
def test_validate_webhook_url_accepts_https_public(_mock_getaddrinfo):
    assert validate_webhook_url("https://callback.example.com/hook") == "https://callback.example.com/hook"


@pytest.mark.parametrize(
    "url",
    [
        "http://callback.example.com/hook",
        "https://127.0.0.1/hook",
        "https://localhost/hook",
        "https://169.254.169.254/latest/meta-data",
        "https://user:pass@callback.example.com/hook",
    ],
)
def test_validate_webhook_url_rejects_unsafe(url):
    with pytest.raises(ValueError):
        validate_webhook_url(url)


def test_normalize_audio_extension_accepts_wav():
    assert normalize_audio_extension("recording.WAV") == ".wav"


def test_normalize_audio_extension_rejects_unknown():
    with pytest.raises(ValueError):
        normalize_audio_extension("malware.exe")


def test_resolve_shared_data_path_stays_under_shared_root(tmp_path):
    shared = tmp_path / "shared_data"
    shared.mkdir()
    job_id = "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
    resolved = resolve_shared_data_path(job_id, ".wav", shared_dir=str(shared))
    assert resolved.startswith(str(shared.resolve()))


def test_validate_shared_path_rejects_traversal(tmp_path):
    shared = tmp_path / "shared_data"
    shared.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError):
        validate_shared_path(str(outside), shared_dir=str(shared))
