#!/usr/bin/env bash
set -uo pipefail

mkdir -p /logs/verifier

app_container="$({
  docker ps \
    --filter label=com.docker.compose.service=app \
    --format '{{.ID}}'
} | head -n 1)"

if [[ -z "$app_container" ]]; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: app container not found" >&2
  exit 1
fi

check_health() {
  curl --silent --fail --max-time 2 http://app:8080/health >/dev/null 2>&1
}

for _ in $(seq 1 15); do
  check_health && break
  sleep 1
done

if ! check_health; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: application process is still flapping" >&2
  exit 1
fi

if ! docker restart "$app_container" >/dev/null; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: app restart failed" >&2
  exit 1
fi

for _ in $(seq 1 15); do
  check_health && break
  sleep 1
done

if ! check_health; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: memory repair did not survive app restart" >&2
  exit 1
fi

echo 1 > /logs/verifier/reward.txt
echo "PASS: application remained healthy before and after restart"
