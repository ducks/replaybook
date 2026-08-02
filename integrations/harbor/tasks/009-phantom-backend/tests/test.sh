#!/usr/bin/env bash
set -uo pipefail

mkdir -p /logs/verifier

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
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: app or backend container not found" >&2
  exit 1
fi

check_health() {
  curl --silent --fail http://app:8080/health >/dev/null 2>&1
}

for _ in $(seq 1 15); do
  check_health && break
  sleep 1
done

if ! check_health; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: application still routes to the wrong backend" >&2
  exit 1
fi

if ! docker restart "$backend_container" "$app_container" >/dev/null; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: service restart failed" >&2
  exit 1
fi

for _ in $(seq 1 15); do
  check_health && break
  sleep 1
done

if ! check_health; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: routing repair did not survive service restarts" >&2
  exit 1
fi

echo 1 > /logs/verifier/reward.txt
echo "PASS: backend routing recovered before and after service restarts"
