#!/usr/bin/env bash
set -euo pipefail

app_container="$({
  docker ps \
    --filter label=com.docker.compose.service=app \
    --format '{{.ID}}'
} | head -n 1)"

if [[ -z "$app_container" ]]; then
  echo "app container not found" >&2
  exit 1
fi

docker cp /solution/app.env "$app_container:/app/.env"
docker restart "$app_container" >/dev/null
