#!/usr/bin/env bash
set -euo pipefail

base_url="${1:?usage: verify.sh BASE_URL PHASE}"
phase="${2:?usage: verify.sh BASE_URL PHASE}"
for _ in $(seq 1 20); do
  body="$(curl --silent --fail --max-time 2 "$base_url/health" || true)"
  [[ "$body" == "ok" ]] && exit 0
  sleep 1
done
echo "$phase verification failed: $base_url/health did not return ok" >&2
exit 1
