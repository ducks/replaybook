#!/usr/bin/env bash
# Success: Sidekiq can connect to Redis (container is up and not restarting)
set -e
CONTAINER=$(docker compose ps -q sidekiq)
STATUS=$(docker inspect --format='{{.RestartCount}}' "$CONTAINER" 2>/dev/null)
RUNNING=$(docker inspect --format='{{.State.Running}}' "$CONTAINER" 2>/dev/null)

[ "$RUNNING" = "true" ] && [ "$STATUS" -lt 2 ]
