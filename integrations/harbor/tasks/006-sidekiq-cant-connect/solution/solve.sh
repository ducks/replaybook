#!/usr/bin/env bash
set -euo pipefail

redis_container="$({
  docker ps \
    --filter label=com.docker.compose.service=redis \
    --format '{{.ID}}'
} | head -n 1)"

docker exec "$redis_container" \
  redis-cli --no-auth-warning -a hunter2 \
  ACL SETUSER default resetpass '>correctpassword' >/dev/null
