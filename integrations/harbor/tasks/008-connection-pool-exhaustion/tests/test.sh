#!/usr/bin/env bash
set -uo pipefail

mkdir -p /logs/verifier

fail() {
  printf '%s\n' "$1" > /logs/verifier/failure-category.txt
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL[$1]: $2" >&2
  exit 1
}

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
  fail topology_changed "database, app, or batch container not found"
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
  fail repair_incomplete "checkout still cannot acquire a database connection"
fi

if ! docker restart "$db_container" "$batch_container" "$app_container" >/dev/null; then
  fail restart_failed "service restart failed"
fi

for _ in $(seq 1 20); do
  check_stable_health && break
  sleep 1
done

if ! check_stable_health; then
  fail repair_not_durable "connection-pool repair did not survive service restarts"
fi

echo 1 > /logs/verifier/reward.txt
echo "PASS: checkout retained database access before and after restarts"
