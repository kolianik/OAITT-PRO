#!/usr/bin/env bash
# Start all OAITT-PRO services. Use this instead of bare `docker compose up -d`.
set -euo pipefail
cd "$(dirname "$0")"

# Daemon preflight: fail fast with a clear message instead of a cryptic HTTP 500.
_server=$(docker version --format '{{.Server.APIVersion}}' 2>/dev/null || true)
if [ -z "$_server" ]; then
    echo "ERROR: Docker daemon is not reachable (no server API version returned)." >&2
    echo "Start or restart the Docker daemon, then re-run this script." >&2
    echo "See TROUBLESHOOTING.md -> Error 1b." >&2
    exit 1
fi
# Pin DOCKER_API_VERSION only when CLI is strictly newer than the server (currently both = 1.54).
_client=$(docker version --format '{{.Client.APIVersion}}' 2>/dev/null || true)
if [ -n "$_client" ] && \
   [ "$(printf '%s\n%s\n' "$_server" "$_client" | sort -V | tail -1)" = "$_client" ] && \
   [ "$_client" != "$_server" ]; then
    export DOCKER_API_VERSION="$_server"
    echo "CLI api $_client > server api $_server; pinned DOCKER_API_VERSION=$_server"
fi

# Resolve the proxy contract (discover system proxy -> probe direct reachability with the
# proxy bypassed -> disable / translate loopback->host.docker.internal / passthrough) and
# export it for Compose + BuildKit. Sourced so it mutates this shell. See scripts/netprep.sh.
# shellcheck disable=SC1091
source "$(dirname "$0")/scripts/netprep.sh"

# Corporate MITM CA (Scenario S3): injected into builds + runtime only when a CA is staged
# AND the operator has opted in (two-factor gate). Default is fail-closed.
if [ "${CORP_CA_AUTO_TRUST:-0}" = "1" ] && ls certs/extra-ca/*.crt >/dev/null 2>&1; then
    export CORP_CA_AUTO_TRUST=1
    export REQUESTS_CA_BUNDLE="${REQUESTS_CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}"
    export SSL_CERT_FILE="${SSL_CERT_FILE:-$REQUESTS_CA_BUNDLE}"
    echo "CORP_CA_AUTO_TRUST=1: corporate CA(s) in certs/extra-ca will be trusted by builds + runtime."
    if [ ! -e /.dockerenv ] && command -v update-ca-certificates >/dev/null 2>&1; then
        echo "Docker daemon image pulls also need the CA at host level (Linux):"
        echo "  sudo cp certs/extra-ca/*.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates && sudo systemctl restart docker"
    fi
else
    export CORP_CA_AUTO_TRUST="${CORP_CA_AUTO_TRUST:-0}"
fi

export DOCKER_BUILDKIT=1
docker compose up -d "$@"
