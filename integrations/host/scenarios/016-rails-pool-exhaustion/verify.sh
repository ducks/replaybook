#!/usr/bin/env bash
set -euo pipefail

base_url="${1:?usage: verify.sh BASE_URL PHASE STATE_DIR}"
phase="${2:?usage: verify.sh BASE_URL PHASE STATE_DIR}"
state_dir="${3:?usage: verify.sh BASE_URL PHASE STATE_DIR}"
failed_file="$state_dir/failed-checkout-ids"

[[ -s "$failed_file" ]] || { echo "$phase verification is missing failed checkout IDs" >&2; exit 23; }
pool_size=""
for _ in $(seq 1 30); do
  pool_size="$(curl --silent --fail --max-time 2 "$base_url/pool" || true)"
  [[ "$pool_size" =~ ^[0-9]+$ ]] && (( pool_size >= 4 )) && break
  sleep 1
done
[[ "$pool_size" =~ ^[0-9]+$ ]] && (( pool_size >= 4 )) || {
  echo "$phase verification found ActiveRecord pool size $pool_size, expected at least 4" >&2
  exit 23
}

while IFS= read -r checkout_id; do
  code="$(curl --silent --output /dev/null --max-time 4 --request POST --write-out '%{http_code}' "$base_url/checkouts/$checkout_id" || true)"
  [[ "$code" == "200" ]] || { echo "$phase verification could not recover checkout $checkout_id" >&2; exit 23; }
done <"$failed_file"

work_dir="$state_dir/verify-$phase"
rm -rf -- "$work_dir"
install -d -m 0700 "$work_dir"
for index in $(seq 1 8); do
  checkout_id="verify-$phase-$(date +%s%N)-$$-$RANDOM-$index"
  (curl --silent --output /dev/null --max-time 5 --request POST --write-out '%{http_code}' \
    "$base_url/checkouts/$checkout_id" >"$work_dir/$index.code" || true) &
done
wait
for index in $(seq 1 8); do
  code="$(<"$work_dir/$index.code")"
  [[ "$code" == "200" ]] || {
    echo "$phase verification found checkout failure under configured concurrency: HTTP $code" >&2
    exit 23
  }
done
