"""Guard tests: every built image injects a staged corporate root CA *only* when
gated (``CORP_CA_AUTO_TRUST=1`` + a staged ``certs/extra-ca/*.crt``), keeps the default
fail-closed, and exposes the merged bundle via standard env vars. Plus: the staging
dir is un-ignored, and host-side ``prepare.py`` uses the CA env (not just the insecure
bypass).

Pure file reads (no Docker), same spirit as ``tests/test_gigaam_docs_consistency.py``.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Debian/conda-based images use update-ca-certificates; all three build from repo root.
DEBIAN_DOCKERFILES = ["gigaam/Dockerfile", "gateway/Dockerfile", "whisperx/Dockerfile"]


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", DEBIAN_DOCKERFILES)
def test_dockerfile_declares_ca_gate(rel):
    text = _read(rel)
    assert "ARG CORP_CA_AUTO_TRUST" in text, f"{rel}: missing ARG CORP_CA_AUTO_TRUST"
    assert "certs/extra-ca" in text, f"{rel}: must COPY certs/extra-ca/ into the image"
    assert "update-ca-certificates" in text, f"{rel}: must run update-ca-certificates when gated"


@pytest.mark.parametrize("rel", DEBIAN_DOCKERFILES)
def test_dockerfile_ca_injection_is_gated_and_fail_closed(rel):
    text = _read(rel)
    # Trust only when the flag is on...
    assert 'CORP_CA_AUTO_TRUST" = "1"' in text, (
        f'{rel}: update-ca-certificates must be guarded by [ "$CORP_CA_AUTO_TRUST" = "1" ]'
    )
    # ...and remove the staged certs in the else-branch (default = no corporate trust).
    assert "rm -f" in text and "extra" in text, f"{rel}: must remove staged certs when not trusted (fail-closed)"


@pytest.mark.parametrize("rel", DEBIAN_DOCKERFILES)
def test_dockerfile_sets_ca_bundle_env(rel):
    text = _read(rel)
    for env in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        assert env in text, f"{rel}: must ENV {env} to the merged ca-certificates bundle"


def test_gigaam_ca_injection_precedes_dependency_apt():
    """In gigaam/Dockerfile the CA must be trusted before the libsndfile/ffmpeg apt-get
    install, otherwise that apt-get update fails under a MITM proxy."""
    text = _read("gigaam/Dockerfile")
    copy_idx = text.find("certs/extra-ca")
    apt_idx = text.find("libsndfile1")
    assert copy_idx != -1 and apt_idx != -1
    assert copy_idx < apt_idx, "CA injection must come before the dependency apt-get install in gigaam/Dockerfile"


def test_gitignore_unignores_extra_ca():
    text = _read(".gitignore")
    assert "!certs/extra-ca/" in text, ".gitignore must un-ignore certs/extra-ca/ so COPY finds the dir"


def test_extra_ca_dir_exists_in_context():
    assert (REPO_ROOT / "certs" / "extra-ca").is_dir(), (
        "certs/extra-ca/ must exist (committed .gitkeep) or COPY fails the build for everyone"
    )


def test_prepare_py_prefers_ca_bundle_over_insecure():
    text = _read("prepare.py")
    assert "REQUESTS_CA_BUNDLE" in text and "SSL_CERT_FILE" in text, (
        "prepare.py must wire the corporate CA via REQUESTS_CA_BUNDLE/SSL_CERT_FILE "
        "(verification stays on) rather than relying solely on PREPARE_INSECURE_SSL"
    )


def test_compose_passes_ca_build_arg():
    text = _read("docker-compose.yml")
    assert "CORP_CA_AUTO_TRUST" in text, "docker-compose.yml must pass CORP_CA_AUTO_TRUST as a build arg"
