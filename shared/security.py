"""Shared path validation and internal service authentication."""

from __future__ import annotations

import ipaddress
import os
import socket
import uuid
from typing import Optional
from urllib.parse import urlparse

ALLOWED_AUDIO_EXTENSIONS = frozenset(
    {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm", ".mp4", ".mpeg", ".mpga"}
)

SHARED_DATA_DIR = "/shared_data"
INTERNAL_TOKEN_HEADER = "X-Internal-Service-Token"


def normalize_audio_extension(filename: Optional[str]) -> str:
    ext = os.path.splitext(filename or "audio.wav")[1].lower() or ".wav"
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise ValueError(
            f"Unsupported audio extension '{ext}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_AUDIO_EXTENSIONS))}"
        )
    if "/" in ext or "\\" in ext or ".." in ext or "\x00" in ext:
        raise ValueError("Invalid file extension")
    return ext


def resolve_shared_data_path(
    job_id: str,
    file_ext: str,
    shared_dir: str = SHARED_DATA_DIR,
) -> str:
    try:
        uuid.UUID(job_id)
    except ValueError as exc:
        raise ValueError("Invalid job_id") from exc

    if file_ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise ValueError("Invalid file extension")

    shared_root = os.path.realpath(shared_dir)
    candidate = os.path.join(shared_root, f"{job_id}{file_ext}")
    resolved = os.path.realpath(candidate)
    prefix = shared_root if shared_root.endswith(os.sep) else shared_root + os.sep
    if not resolved.startswith(prefix):
        raise ValueError("Path escapes shared storage")
    return resolved


def validate_shared_path(path: str, shared_dir: str = SHARED_DATA_DIR) -> str:
    if not path or "\x00" in path:
        raise ValueError("Invalid path")

    shared_root = os.path.realpath(shared_dir)
    resolved = os.path.realpath(path)
    prefix = shared_root if shared_root.endswith(os.sep) else shared_root + os.sep
    if not resolved.startswith(prefix):
        raise ValueError("Path must be under shared storage")
    if not os.path.isfile(resolved):
        raise ValueError("File not found on shared storage")
    return resolved


def safe_remove_shared_path(path: str, shared_dir: str = SHARED_DATA_DIR) -> None:
    resolved = validate_shared_path(path, shared_dir)
    os.remove(resolved)


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return True
    if ip_str == "169.254.169.254":
        return True
    return False


def validate_webhook_url(url: str) -> str:
    if not url or not url.strip():
        raise ValueError("webhook_url must not be empty")

    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        raise ValueError("webhook_url must use HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("webhook_url must not contain credentials")
    if not parsed.hostname:
        raise ValueError("webhook_url must include a hostname")

    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "0.0.0.0"} or hostname.endswith(".local"):
        raise ValueError("webhook_url hostname is not allowed")

    if _is_blocked_ip(hostname):
        raise ValueError("webhook_url must not target private or metadata addresses")

    try:
        addr_infos = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("webhook_url hostname could not be resolved") from exc

    for info in addr_infos:
        if _is_blocked_ip(info[4][0]):
            raise ValueError("webhook_url resolves to a private or metadata address")

    return url.strip()


def get_internal_service_token() -> str:
    return os.getenv("INTERNAL_SERVICE_TOKEN", "")


def internal_service_headers() -> dict[str, str]:
    token = get_internal_service_token()
    if not token:
        return {}
    return {INTERNAL_TOKEN_HEADER: token}


def verify_internal_service_token(header_value: Optional[str]) -> None:
    expected = get_internal_service_token()
    if not expected:
        return
    if not header_value or header_value != expected:
        raise PermissionError("Invalid internal service token")
