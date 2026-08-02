#!/usr/bin/env bash
set -uo pipefail

mkdir -p /logs/verifier

redis_container="$({
  docker ps \
    --filter label=com.docker.compose.service=redis \
    --format '{{.ID}}'
} | head -n 1)"
sidekiq_container="$({
  docker ps \
    --filter label=com.docker.compose.service=sidekiq \
    --format '{{.ID}}'
} | head -n 1)"

if [[ -z "$redis_container" || -z "$sidekiq_container" ]]; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: redis or sidekiq container not found" >&2
  exit 1
fi

last_redis_response=""

check_write() {
  local response

  if ! response="$(
    docker exec "$sidekiq_container" \
      redis-cli --no-auth-warning --raw \
      -u redis://default:correctpassword@redis:6379/0 \
      SET replaybook:verifier "$(date +%s)" 2>&1
  )"; then
    last_redis_response="$response"
    return 1
  fi

  last_redis_response="$response"
  [[ "$response" == "OK" ]]
}

for _ in $(seq 1 15); do
  check_write && break
  sleep 1
done

if ! check_write; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: worker's configured Redis connection cannot write" >&2
  printf 'Redis replied: %q\n' "$last_redis_response" >&2
  exit 1
fi

if ! docker restart "$redis_container" "$sidekiq_container" >/dev/null; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: service restart failed" >&2
  exit 1
fi

for _ in $(seq 1 15); do
  check_write && break
  sleep 1
done

if ! check_write; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: Redis repair did not survive service restarts" >&2
  printf 'Redis replied: %q\n' "$last_redis_response" >&2
  exit 1
fi

echo 1 > /logs/verifier/reward.txt
echo "PASS: background worker wrote to Redis before and after service restarts"
