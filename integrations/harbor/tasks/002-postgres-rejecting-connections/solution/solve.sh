#!/usr/bin/env bash
set -euo pipefail

db_container="$({
  docker ps \
    --filter label=com.docker.compose.service=db \
    --format '{{.ID}}'
} | head -n 1)"

if [[ -z "$db_container" ]]; then
  echo "database container not found" >&2
  exit 1
fi

docker exec "$db_container" \
  sed -i '/^host all all all reject$/d' /var/lib/postgresql/data/pg_hba.conf
docker exec "$db_container" psql -U postgres -d appdb \
  -c 'SELECT pg_reload_conf()' >/dev/null
