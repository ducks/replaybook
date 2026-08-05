#!/usr/bin/env bash
set -uo pipefail

mkdir -p /logs/verifier

fail() {
  printf '%s\n' "$1" > /logs/verifier/failure-category.txt
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL[$1]: $2" >&2
  exit 1
}

wait_for_health() {
  for _ in $(seq 1 10); do
    if [[ "$(curl --silent --output /dev/null --write-out '%{http_code}' http://nginx/health)" == "200" ]]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

if ! wait_for_health; then
  fail repair_incomplete "nginx health endpoint did not return HTTP 200"
fi

app_container="$({
  docker ps \
    --filter label=com.docker.compose.service=app \
    --format '{{.ID}}'
} | head -n 1)"

nginx_container="$({
  docker ps \
    --filter label=com.docker.compose.service=nginx \
    --format '{{.ID}}'
} | head -n 1)"

if [[ -z "$app_container" || -z "$nginx_container" ]]; then
  fail topology_changed "could not identify app and nginx containers"
fi

# A successful response is not enough. Restart both services so a temporary
# process or in-memory change cannot satisfy the verifier.
if ! docker restart "$app_container" >/dev/null ||
   ! docker restart "$nginx_container" >/dev/null ||
   ! wait_for_health; then
  fail repair_not_durable "health did not survive service restarts"
fi

echo 1 > /logs/verifier/reward.txt
echo "PASS: nginx health endpoint returned HTTP 200 before and after service restarts"
exit 0
