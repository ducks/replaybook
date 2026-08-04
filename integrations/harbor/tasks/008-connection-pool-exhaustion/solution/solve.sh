#!/usr/bin/env bash
set -euo pipefail

batch_container="$({
  docker ps \
    --filter label=com.docker.compose.service=batch \
    --format '{{.ID}}'
} | head -n 1)"

docker exec "$batch_container" pkill -f /app/leak.py
