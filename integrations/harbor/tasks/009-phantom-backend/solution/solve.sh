#!/usr/bin/env bash
set -euo pipefail

app_container="$({
  docker ps \
    --filter label=com.docker.compose.service=app \
    --format '{{.ID}}'
} | head -n 1)"

docker exec "$app_container" sh -c \
  'grep -v " backend " /etc/hosts > /tmp/hosts.clean && cat /tmp/hosts.clean > /etc/hosts && rm /tmp/hosts.clean'
