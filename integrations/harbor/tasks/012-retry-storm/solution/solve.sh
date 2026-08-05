#!/usr/bin/env bash
set -euo pipefail

app_container="$({
  docker ps --filter label=com.docker.compose.service=app --format '{{.ID}}'
} | head -n 1)"

docker cp /solution/retry.conf "$app_container:/app/retry.conf"
docker restart "$app_container" >/dev/null
