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
  docker ps --filter label=com.docker.compose.service=app --format '{{.ID}}'
} | head -n 1)"
[[ -n "$app_container" ]] || fail topology_changed "app container not found"

check_access() {
  local current old
  current="$(curl --silent --output /dev/null --write-out '%{http_code}' \
    --header 'Authorization: Bearer credential-after-rotation' \
    http://app:8080/private)"
  old="$(curl --silent --output /dev/null --write-out '%{http_code}' \
    --header 'Authorization: Bearer credential-before-rotation' \
    http://app:8080/private)"
  [[ "$current" == "200" && "$old" == "401" ]]
}

for _ in $(seq 1 15); do check_access && break; sleep 1; done
if ! check_access; then
  current_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
    --header 'Authorization: Bearer credential-after-rotation' \
    http://app:8080/private)"
  if [[ "$current_code" == "200" ]]; then
    fail regression "the retired credential still grants access"
  fi
  fail repair_incomplete "the rotated credential cannot access the private route"
fi

docker restart "$app_container" >/dev/null || fail restart_failed "app restart failed"
for _ in $(seq 1 15); do check_access && break; sleep 1; done
check_access || fail repair_not_durable "authentication repair did not survive restart"

echo 1 > /logs/verifier/reward.txt
echo "PASS: rotated credentials work and retired credentials remain rejected after restart"
