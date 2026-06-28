#!/usr/bin/env bash
set -e

SCENARIO_DIR="$(cd "$(dirname "$0")" && pwd)"
CONTAINER=$(docker compose -f "$SCENARIO_DIR/docker-compose.yml" ps -q nginx)

docker cp "$SCENARIO_DIR/nginx-broken.conf" "$CONTAINER:/etc/nginx/nginx.conf"
docker compose -f "$SCENARIO_DIR/docker-compose.yml" exec -T nginx nginx -s reload
