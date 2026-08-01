#!/usr/bin/env bash
set -euo pipefail

nginx_container="$({
  docker ps \
    --filter label=com.docker.compose.service=nginx \
    --format '{{.ID}}'
} | head -n 1)"

if [[ -z "$nginx_container" ]]; then
  echo "nginx service container not found" >&2
  exit 1
fi

docker cp /solution/nginx.conf "$nginx_container:/etc/nginx/nginx.conf"
docker exec "$nginx_container" nginx -s reload
