#!/usr/bin/env bash
set -euo pipefail

app_container="$({
  docker ps --filter label=com.docker.compose.service=app-b --format '{{.ID}}'
} | head -n 1)"

docker cp /solution/release.env "$app_container:/app/release.env"
docker restart "$app_container" >/dev/null
