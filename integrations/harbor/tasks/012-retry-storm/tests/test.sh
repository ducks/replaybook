#!/usr/bin/env bash
set -uo pipefail

mkdir -p /logs/verifier
source /tests/topology.sh

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

for service_container in \
  "app:$app_container" \
  "primary:$primary_container" \
  "fallback:$fallback_container"; do
  service="${service_container%%:*}"
  container="${service_container#*:}"
  if violation="$(compose_contract_violation "$container" "$service")"; then
    fail topology_changed "$violation"
  fi
done

app_project="$(compose_project "$app_container")"
primary_project="$(compose_project "$primary_container")"
fallback_project="$(compose_project "$fallback_container")"
if [[ "$app_project" != "$primary_project" || "$app_project" != "$fallback_project" ]]; then
  fail topology_changed "app, primary, and fallback no longer share a Compose project"
fi

for project_label in \
  com.docker.compose.project.config_files \
  com.docker.compose.project.working_dir \
  com.docker.compose.version; do
  app_value="$(compose_label "$app_container" "$project_label")"
  primary_value="$(compose_label "$primary_container" "$project_label")"
  fallback_value="$(compose_label "$fallback_container" "$project_label")"
  if [[ "$app_value" != "$primary_value" || "$app_value" != "$fallback_value" ]]; then
    fail topology_changed "Compose project metadata differs across deployed services"
  fi
done

if unresolved_service="$(first_unresolved_service app primary fallback)"; then
  fail topology_changed "expected service address does not resolve: $unresolved_service"
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
