#!/usr/bin/env bash
# Seed GigaAM model cache: bootstrap in running container, export volume to tar.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "WARNING: HF_TOKEN not set — Pyannote diarization prefetch will be skipped." >&2
fi

echo "Starting gigaam-service for bootstrap (wait until healthy)..."
docker compose up -d gigaam-service

echo "Waiting for healthy status (may take up to an hour on first run)..."
for i in $(seq 1 120); do
  status=$(docker inspect --format='{{.State.Health.Status}}' oaitt-gigaam 2>/dev/null || echo "unknown")
  if [[ "$status" == "healthy" ]]; then
    echo "gigaam-service is healthy."
    break
  fi
  if [[ "$status" == "unhealthy" ]]; then
    echo "Health log:" >&2
    docker inspect --format='{{range .State.Health.Log}}{{.Output}}{{end}}' oaitt-gigaam >&2 || true
    echo "Container failed bootstrap. Fix errors and retry." >&2
    exit 1
  fi
  sleep 30
done

VOL_NAME=$(docker volume ls --format '{{.Name}}' | grep gigaam_model_cache | head -1)
if [[ -z "$VOL_NAME" ]]; then
  echo "ERROR: gigaam_model_cache volume not found" >&2
  exit 1
fi

OUT="gigaam_cache.tar.gz"
echo "Exporting volume $VOL_NAME to $OUT ..."
docker run --rm -v "${VOL_NAME}:/data" -v "$(pwd):/backup" alpine \
  tar czf "/backup/${OUT}" -C /data .

cat <<EOF

Export complete: ${OUT}

Import on air-gapped host:
  docker volume create <project>_gigaam_model_cache
  docker run --rm -v <project>_gigaam_model_cache:/data -v \$(pwd):/backup alpine \\
    tar xzf /backup/${OUT} -C /data
  # In .env: GIGAAM_OFFLINE_MODE=true
  docker compose up -d gigaam-service

EOF
