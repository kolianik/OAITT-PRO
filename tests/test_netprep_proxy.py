"""Guard + unit tests for the shared network-preflight ``scripts/netprep.sh``.

``netprep`` is *sourced* by ``start.sh`` / ``build-gigaam.sh`` and resolves the proxy
contract that flows into Compose and BuildKit. These tests drive its pure helpers via
``bash`` so the loopback→host.docker.internal translation, the canonical NO_PROXY list,
and the "direct reachable ⇒ disable proxy" fast-path are pinned regardless of host.

Sourcing with ``OAITT_NETPREP_NO_AUTORUN=1`` defines the functions without running the
discover→probe→decide pipeline, so individual helpers can be exercised in isolation.
Tests that need ``bash`` skip cleanly where it is unavailable (kept runnable under a
bare ``pytest`` on both Linux prod and the Windows/Git-Bash dev host).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
NETPREP_SH = REPO_ROOT / "scripts" / "netprep.sh"

bash = shutil.which("bash")
requires_bash = pytest.mark.skipif(bash is None, reason="bash not available on this host")


def _run(snippet: str) -> str:
    """Source netprep (no autorun) then run the snippet; return stdout (stripped)."""
    script = f'OAITT_NETPREP_NO_AUTORUN=1 . "{NETPREP_SH.as_posix()}"\n{snippet}\n'
    res = subprocess.run(
        [bash, "-c", script],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT.as_posix(),
    )
    assert res.returncode == 0, f"snippet failed: {res.returncode}\nSTDERR:\n{res.stderr}"
    return res.stdout.strip()


def test_netprep_script_exists():
    assert NETPREP_SH.is_file(), "scripts/netprep.sh must exist"
    text = NETPREP_SH.read_text(encoding="utf-8")
    # Must expose the pure helpers and an autorun guard.
    for fn in ("netprep_translate_loopback", "netprep_compute_no_proxy", "netprep_probe_direct"):
        assert fn in text, f"netprep.sh must define {fn}"
    assert "OAITT_NETPREP_NO_AUTORUN" in text, "netprep.sh must support a no-autorun guard"


@requires_bash
@pytest.mark.parametrize(
    "given,expected",
    [
        ("http://127.0.0.1:10808", "http://host.docker.internal:10808"),
        ("http://localhost:3128", "http://host.docker.internal:3128"),
        ("http://[::1]:8080", "http://host.docker.internal:8080"),
        ("http://127.0.0.1", "http://host.docker.internal:80"),  # port-less → 80
    ],
)
def test_loopback_is_translated(given, expected):
    assert _run(f'netprep_translate_loopback "{given}"') == expected


@requires_bash
@pytest.mark.parametrize("given", ["http://10.0.0.5:3128", "http://proxy.corp.example:8080"])
def test_non_loopback_passes_through(given):
    assert _run(f'netprep_translate_loopback "{given}"') == given


@requires_bash
def test_no_proxy_lists_all_internal_targets():
    out = _run("netprep_compute_no_proxy")
    for needle in (
        "localhost",
        "127.0.0.1",
        "::1",
        "host.docker.internal",
        "gateway-orchestrator",
        "whisperx-service",
        "gigaam-service",
        "postgres",
        "front-proxy",
        ".local",
    ):
        assert needle in out, f"NO_PROXY must include {needle}: got {out!r}"


@requires_bash
def test_direct_reachable_disables_proxy():
    """When the direct probe succeeds, all proxy vars are emptied and mode=direct,
    even though a (loopback) proxy was configured."""
    snippet = (
        'export HTTP_PROXY=http://127.0.0.1:10808 HTTPS_PROXY=http://127.0.0.1:10808\n'
        'netprep_probe_direct() { return 0; }\n'  # force "direct works"
        'netprep_main >/dev/null 2>&1\n'
        'echo "mode=$OAITT_PROXY_MODE http=[$HTTP_PROXY] https=[$HTTPS_PROXY]"\n'
    )
    out = _run(snippet)
    assert "mode=direct" in out, out
    assert "http=[]" in out and "https=[]" in out, f"proxy must be disabled on direct: {out!r}"


@requires_bash
def test_loopback_proxy_translated_when_direct_fails():
    """Direct fails but the proxy works → loopback translated, mode=translated."""
    snippet = (
        'export HTTP_PROXY=http://127.0.0.1:10808 HTTPS_PROXY=http://127.0.0.1:10808\n'
        'netprep_probe_direct() { return 1; }\n'      # direct blocked
        'netprep_probe_proxy() { return 0; }\n'       # proxy works
        'netprep_main >/dev/null 2>&1\n'
        'echo "mode=$OAITT_PROXY_MODE http=[$HTTP_PROXY]"\n'
    )
    out = _run(snippet)
    assert "mode=translated" in out, out
    assert "http=[http://host.docker.internal:10808]" in out, out
