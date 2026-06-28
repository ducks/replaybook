#!/usr/bin/env bash
set -e

SCENARIO_DIR="$(cd "$(dirname "$0")" && pwd)"

# Fill the container's filesystem leaving no space
docker compose -f "$SCENARIO_DIR/docker-compose.yml" exec -T app \
  dd if=/dev/zero of=/tmp/bigfile bs=1M count=28 2>/dev/null || true
