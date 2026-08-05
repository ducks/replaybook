#!/usr/bin/env bash
set -uo pipefail

mkdir -p /logs/verifier

fail() {
  printf '%s\n' "$1" > /logs/verifier/failure-category.txt
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL[$1]: $2" >&2
  exit 1
}

app_container="$({
  docker ps \
    --filter label=com.docker.compose.service=app \
    --format '{{.ID}}'
} | head -n 1)"
backend_container="$({
  docker ps \
    --filter label=com.docker.compose.service=backend \
    --format '{{.ID}}'
} | head -n 1)"

if [[ -z "$app_container" || -z "$backend_container" ]]; then
  fail topology_changed "app or backend container not found"
fi

check_health() {
  curl --silent --fail http://app:8080/health >/dev/null 2>&1
}

for _ in $(seq 1 15); do
  check_health && break
  sleep 1
done

if ! check_health; then
  fail repair_incomplete "application still routes to the wrong backend"
fi

if ! docker restart "$backend_container" "$app_container" >/dev/null; then
  fail restart_failed "service restart failed"
fi

for _ in $(seq 1 15); do
  check_health && break
  sleep 1
done

if ! check_health; then
  fail repair_not_durable "routing repair did not survive service restarts"
fi

echo 1 > /logs/verifier/reward.txt
echo "PASS: backend routing recovered before and after service restarts"
