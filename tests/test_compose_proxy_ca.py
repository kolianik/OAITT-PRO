"""Guard tests: docker-compose.yml wires proxy build-args, host-gateway, a complete
NO_PROXY exclusion list, and the corporate-CA mount/env onto the right services.

Dependency-free (raw-text block extraction) so it runs under a bare ``pytest`` like
``tests/test_gigaam_docs_consistency.py``. Rationale lives in the plan / SECURITY.md:
BuildKit does not inherit the host proxy (needs ``build.args``), ``host.docker.internal``
needs ``host-gateway`` on Linux for build + runtime, NO_PROXY must exclude every internal
service, and the corporate CA is consumed via standard env vars.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

# Services that are built from a Dockerfile in this repo (need proxy + CA build args).
BUILD_SERVICES = ["gateway-orchestrator", "whisperx-service", "gigaam-service", "front-proxy"]

PROXY_ARG_KEYS = ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"]


def _service_block(name: str) -> str:
    """Return the YAML text of a single top-level service (2-space indent)."""
    lines = COMPOSE.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.rstrip() == f"  {name}:":
            start = i
            break
    assert start is not None, f"service {name} not found in docker-compose.yml"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        # next top-level service header at exactly 2-space indent, or end of services map
        if re.match(r"^ {2}\S", lines[j]) and lines[j].rstrip().endswith(":") and not lines[j].startswith("    "):
            end = j
            break
        if re.match(r"^\S", lines[j]):  # dedented to column 0 (e.g. `volumes:`)
            end = j
            break
    return "\n".join(lines[start:end])


@pytest.mark.parametrize("svc", BUILD_SERVICES)
def test_build_services_pass_proxy_and_ca_args(svc):
    block = _service_block(svc)
    assert "build:" in block and "args:" in block, f"{svc} build.args missing"
    for key in PROXY_ARG_KEYS:
        assert re.search(rf"\b{re.escape(key)}\s*:", block), f"{svc} build.args missing {key}"
    assert "CORP_CA_AUTO_TRUST" in block, f"{svc} must pass CORP_CA_AUTO_TRUST build arg"


@pytest.mark.parametrize("svc", BUILD_SERVICES)
def test_build_services_have_host_gateway(svc):
    block = _service_block(svc)
    assert "host.docker.internal:host-gateway" in block, (
        f"{svc} needs build.extra_hosts host.docker.internal:host-gateway "
        "(Linux BuildKit cannot resolve host.docker.internal otherwise)"
    )


def test_gigaam_no_proxy_default_is_complete():
    block = _service_block("gigaam-service")
    m = re.search(r"NO_PROXY=\$\{NO_PROXY:-([^}]*)\}", block)
    assert m, "gigaam-service must keep a NO_PROXY default with internal exclusions"
    default = m.group(1)
    for needle in (
        "host.docker.internal",
        "gateway-orchestrator",
        "whisperx-service",
        "gigaam-service",
        "postgres",
        "127.0.0.1",
    ):
        assert needle in default, f"NO_PROXY default must exclude {needle}: {default!r}"


def test_gigaam_mounts_corp_ca_and_sets_bundle_env():
    block = _service_block("gigaam-service")
    assert "certs/extra-ca" in block, "gigaam-service must bind-mount ./certs/extra-ca"
    for env in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        assert env in block, f"gigaam-service must set {env} so HF + the Sber-CDN urllib path trust the CA"
