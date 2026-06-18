#!/usr/bin/env bash
# Build gigaam-service image (deps only; models bootstrap on first container start).
set -euo pipefail
cd "$(dirname "$0")"
export DOCKER_BUILDKIT=1

# Daemon preflight: fail fast with a clear message instead of a cryptic HTTP 500.
_server=$(docker version --format '{{.Server.APIVersion}}' 2>/dev/null || true)
if [ -z "$_server" ]; then
    echo "ERROR: Docker daemon is not reachable. Start/restart the Docker daemon, then re-run." >&2
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

# Resolve the proxy contract (discover -> probe direct -> disable/translate/passthrough) and
# export it for BuildKit build-args. Sourced so it mutates this shell. See scripts/netprep.sh.
# shellcheck disable=SC1091
source "$(dirname "$0")/scripts/netprep.sh"

# Corporate MITM CA (Scenario S3): build-arg gate. Trusted only when staged AND opted in.
if [ "${CORP_CA_AUTO_TRUST:-0}" = "1" ] && ls certs/extra-ca/*.crt >/dev/null 2>&1; then
    export CORP_CA_AUTO_TRUST=1
    echo "CORP_CA_AUTO_TRUST=1: corporate CA(s) in certs/extra-ca will be trusted at build time."
else
    export CORP_CA_AUTO_TRUST="${CORP_CA_AUTO_TRUST:-0}"
fi

docker compose build "$@" gigaam-service
