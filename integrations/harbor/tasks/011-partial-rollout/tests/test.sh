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

nginx_container="$(container_for nginx)"
app_a_container="$(container_for app-a)"
app_b_container="$(container_for app-b)"
if [[ -z "$nginx_container" || -z "$app_a_container" || -z "$app_b_container" ]]; then
  fail topology_changed "nginx and both application replicas must remain deployed"
fi

if ! docker exec "$nginx_container" grep -q 'server app-a:8080' /etc/nginx/nginx.conf ||
   ! docker exec "$nginx_container" grep -q 'server app-b:8080' /etc/nginx/nginx.conf; then
  fail constraint_violation "both application replicas must remain in load-balancer rotation"
fi

check_consistent() {
  [[ "$(curl --silent --fail http://app-a:8080/checkout)" == "checkout-v2" ]] || return 1
  [[ "$(curl --silent --fail http://app-b:8080/checkout)" == "checkout-v2" ]] || return 1
  for _ in $(seq 1 6); do
    [[ "$(curl --silent --fail http://nginx/checkout)" == "checkout-v2" ]] || return 1
  done
}

for _ in $(seq 1 15); do check_consistent && break; sleep 1; done
check_consistent || fail repair_incomplete "checkout remains inconsistent across replicas"

docker restart "$app_a_container" "$app_b_container" "$nginx_container" >/dev/null ||
  fail restart_failed "service restart failed"
for _ in $(seq 1 20); do check_consistent && break; sleep 1; done
check_consistent || fail repair_not_durable "rollout repair did not survive service restarts"

echo 1 > /logs/verifier/reward.txt
echo "PASS: both replicas serve the compatible checkout API before and after restart"
