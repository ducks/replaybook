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

if [[ -z "$app_container" ]]; then
  fail topology_changed "app container not found"
fi

check_health() {
  curl --silent --fail --max-time 2 http://app:8080/health >/dev/null 2>&1
}

for _ in $(seq 1 15); do
  check_health && break
  sleep 1
done

if ! check_health; then
  fail repair_incomplete "application process is still flapping"
fi

if ! docker restart "$app_container" >/dev/null; then
  fail restart_failed "app restart failed"
fi

for _ in $(seq 1 15); do
  check_health && break
  sleep 1
done

if ! check_health; then
  fail repair_not_durable "memory repair did not survive app restart"
fi

echo 1 > /logs/verifier/reward.txt
echo "PASS: application remained healthy before and after restart"
