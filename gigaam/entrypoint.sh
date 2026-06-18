#!/bin/bash
set -euo pipefail

# Docker named volumes mount as root; bootstrap runs as oaitt and writes under /app/data.
for dir in /app/data /shared_data; do
  mkdir -p "$dir"
  chown -R oaitt:oaitt "$dir"
done

mkdir -p /tmp/mplconfig
chown oaitt:oaitt /tmp/mplconfig

# runuser resets HOME to passwd (/home/oaitt); DeepFilterNet init_df needs a writable HOME.
exec runuser -u oaitt -- env HOME=/app MPLCONFIGDIR=/tmp/mplconfig uvicorn main:app --host 0.0.0.0 --port 9007
