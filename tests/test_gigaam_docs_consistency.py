"""Guard tests: user-facing *start/restart* docs must route through the canonical
``start.ps1`` / ``start.sh`` scripts, never a bare ``docker compose up -d``.

Why this matters: ``docker-compose.yml`` passes the host shell's ``HTTP_PROXY`` /
``HTTPS_PROXY`` straight into ``gigaam-service`` (``HTTP_PROXY=${HTTP_PROXY:-}``).
A host-local *loopback* proxy (``http://127.0.0.1:<port>``) is unreachable from
inside the container, so a literal ``docker compose up -d`` strands the cold-start
bootstrap with ``[Errno 111] Connection refused``. ``start.ps1`` / ``start.sh`` strip
loopback proxy vars before invoking Compose (CHANGELOG BUG-002b), so the docs must
point at them. These tests are pure file reads (no model deps) and run under a bare
``pytest``.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def test_install_md_uses_canonical_start_scripts():
    text = _read("INSTALL.md")
    # The contract spec must never *instruct* the bare start command: a build-script
    # invocation immediately followed by `docker compose up -d` is the exact bug
    # pattern. Explanatory prose that names the anti-pattern, and the descriptive
    # "On first `docker compose up`" form (no `-d`), are intentionally allowed.
    ps_bug = re.search(r"\.\\build-gigaam\.ps1\s*\n\s*docker compose up -d", text)
    sh_bug = re.search(r"\./build-gigaam\.sh\s*\n\s*docker compose up -d", text)
    assert ps_bug is None and sh_bug is None, (
        "INSTALL.md still instructs `build-gigaam` then a bare `docker compose up -d`; "
        "the canonical start path is `start.ps1` / `start.sh` (strips loopback "
        "proxies before Compose, BUG-002b)."
    )
    assert "start.ps1" in text and "start.sh" in text, (
        "INSTALL.md must document the canonical start scripts start.ps1 / start.sh."
    )


def test_readme_does_not_instruct_bare_start():
    text = _read("README.md")
    # Quickstart "Start the stack" step must not use a bare `docker compose up -d`
    # (`--build` variant included). Descriptive `docker compose up` prose (no `-d`)
    # is allowed.
    assert "docker compose up -d" not in text, (
        "README.md quickstart still uses a bare `docker compose up -d`; route the "
        "start step through ./start.sh (or build-gigaam + start)."
    )


def test_deployment_info_does_not_instruct_bare_start():
    text = _read("DEPLOYMENT_INFO.md")
    assert "docker compose up -d" not in text, (
        "DEPLOYMENT_INFO.md still uses `(sudo) docker compose up -d`; use "
        "`sudo ./start.sh` so loopback proxies are stripped before Compose."
    )


def test_troubleshooting_no_longer_recommends_setting_http_proxy_to_bootstrap():
    text = _read("TROUBLESHOOTING.md")
    # The bootstrap-progress row used to read "Wait; ensure internet or `HTTP_PROXY`",
    # which tempts users to set the very loopback proxy that breaks bootstrap.
    assert "ensure internet or `HTTP_PROXY`" not in text, (
        "TROUBLESHOOTING.md still suggests setting `HTTP_PROXY` to fix a stalled "
        "bootstrap; a loopback proxy is unreachable from the container — reword to "
        "point at start.ps1 / start.sh (use host.docker.internal for a host proxy)."
    )


def test_install_documents_network_scenarios():
    text = _read("INSTALL.md")
    for needle in ("OAITT_PROXY_MODE", "CORP_CA_AUTO_TRUST", "host.docker.internal", "netprep"):
        assert needle in text, (
            f"INSTALL.md must document the clean-install network scenarios (missing {needle!r})."
        )


def test_env_example_has_corp_ca_flag():
    text = _read(".env.example")
    assert "CORP_CA_AUTO_TRUST" in text, (
        ".env.example must document the corporate-CA trust flag CORP_CA_AUTO_TRUST."
    )


def test_troubleshooting_documents_corp_ca_flow():
    text = _read("TROUBLESHOOTING.md")
    assert "detect-corp-ca" in text and "CORP_CA_AUTO_TRUST" in text, (
        "TROUBLESHOOTING.md must point corporate-MITM users at detect-corp-ca + CORP_CA_AUTO_TRUST."
    )
