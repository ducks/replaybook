#!/usr/bin/env bash
set -euo pipefail

base_url="${1:?usage: preflight.sh BASE_URL STATE_DIR}"
state_dir="${2:?usage: preflight.sh BASE_URL STATE_DIR}"
failed_file="$state_dir/failed-checkout-ids"
work_dir="$state_dir/preflight-requests"

install -d -m 0700 "$state_dir" "$work_dir"
rm -f -- "$failed_file" "$work_dir"/*

for _ in $(seq 1 60); do
  [[ "$(curl --silent --fail --max-time 2 "$base_url/health" 2>/dev/null || true)" == "ok" ]] && break
  sleep 1
done
[[ "$(curl --silent --fail --max-time 2 "$base_url/health" 2>/dev/null || true)" == "ok" ]]
[[ "$(curl --silent --fail --max-time 2 "$base_url/pool")" == "1" ]] || {
  echo "expected an undersized ActiveRecord pool before repair" >&2
  exit 1
}

for index in 1 2 3 4; do
  checkout_id="preflight-$(date +%s%N)-$$-$RANDOM-$index"
  printf '%s\n' "$checkout_id" >"$work_dir/$index.id"
  (curl --silent --output /dev/null --max-time 4 --request POST --write-out '%{http_code}' \
    "$base_url/checkouts/$checkout_id" >"$work_dir/$index.code" || true) &
done
wait

: >"$failed_file"
passed=0
for index in 1 2 3 4; do
  checkout_id="$(<"$work_dir/$index.id")"
  code="$(<"$work_dir/$index.code")"
  if [[ "$code" == "200" ]]; then
    passed=$((passed + 1))
  else
    printf '%s\n' "$checkout_id" >>"$failed_file"
  fi
done
(( passed >= 1 && passed < 4 )) || {
  echo "expected partial checkout failures from pool exhaustion, got $passed/4 successes" >&2
  exit 1
}
[[ -s "$failed_file" ]]
chmod 0600 "$failed_file"
