#!/usr/bin/env bash
set -uo pipefail

mkdir -p /logs/verifier

db_container="$({
  docker ps --filter label=com.docker.compose.service=db --format '{{.ID}}'
} | head -n 1)"
app_container="$({
  docker ps --filter label=com.docker.compose.service=app --format '{{.ID}}'
} | head -n 1)"
batch_container="$({
  docker ps --filter label=com.docker.compose.service=batch --format '{{.ID}}'
} | head -n 1)"

if [[ -z "$db_container" || -z "$app_container" || -z "$batch_container" ]]; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: database, app, or batch container not found" >&2
  exit 1
fi

check_health() {
  curl --silent --fail --max-time 4 http://app:8080/health >/dev/null 2>&1
}

check_stable_health() {
  for _ in $(seq 1 3); do
    check_health || return 1
    sleep 1
  done
}

for _ in $(seq 1 15); do
  check_stable_health && break
  sleep 1
done

if ! check_stable_health; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: checkout still cannot acquire a database connection" >&2
  exit 1
fi

if ! docker restart "$db_container" "$batch_container" "$app_container" >/dev/null; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: service restart failed" >&2
  exit 1
fi

for _ in $(seq 1 20); do
  check_stable_health && break
  sleep 1
done

if ! check_stable_health; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: connection-pool repair did not survive service restarts" >&2
  exit 1
fi

echo 1 > /logs/verifier/reward.txt
echo "PASS: checkout retained database access before and after restarts"
