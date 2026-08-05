#!/usr/bin/env bash
set -uo pipefail

mkdir -p /logs/verifier

fail() {
  printf '%s\n' "$1" > /logs/verifier/failure-category.txt
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL[$1]: $2" >&2
  exit 1
}

container_for() {
  docker ps --filter "label=com.docker.compose.service=$1" --format '{{.ID}}' | head -n 1
}

app_container="$(container_for app)"
primary_container="$(container_for primary)"
fallback_container="$(container_for fallback)"
if [[ -z "$app_container" || -z "$primary_container" || -z "$fallback_container" ]]; then
  fail topology_changed "app, primary, and fallback services must remain deployed"
fi

retry_count="$(docker exec "$app_container" sh -c \
  "sed -n 's/^MAX_PRIMARY_RETRIES=//p' /app/retry.conf")"
if [[ ! "$retry_count" =~ ^[0-9]+$ || "$retry_count" -lt 1 ]]; then
  fail constraint_violation "the primary must receive at least one attempt before fallback"
fi

check_slo() {
  local result body duration
  result="$(curl --silent --show-error --max-time 3 \
    --write-out $'\n%{time_total}' http://app:8080/checkout)" || return 1
  body="${result%$'\n'*}"
  duration="${result##*$'\n'}"
  [[ "$body" == "checkout-ok:pong" ]] || return 1
  awk -v duration="$duration" 'BEGIN { exit !(duration < 0.8) }'
}

for _ in $(seq 1 10); do check_slo && break; sleep 1; done
check_slo || fail repair_incomplete "checkout remains outside the 800 ms latency SLO"

docker restart "$primary_container" "$fallback_container" "$app_container" >/dev/null ||
  fail restart_failed "service restart failed"
for _ in $(seq 1 15); do check_slo && break; sleep 1; done
check_slo || fail repair_not_durable "retry repair did not survive service restarts"

echo 1 > /logs/verifier/reward.txt
echo "PASS: checkout uses fallback within the latency SLO before and after restart"
