#!/usr/bin/env bash
set -euo pipefail

app_container="$({
  docker ps \
    --filter label=com.docker.compose.service=app \
    --format '{{.ID}}'
} | head -n 1)"

docker exec "$app_container" rm -f /tmp/app-debug.core
