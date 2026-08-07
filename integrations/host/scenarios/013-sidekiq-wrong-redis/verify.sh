#!/usr/bin/env bash
set -euo pipefail

base_url="${1:?usage: verify.sh BASE_URL PHASE}"
phase="${2:?usage: verify.sh BASE_URL PHASE}"
job_id="verify-$phase-$(date +%s)-$$"

code="000"
for _ in $(seq 1 20); do
  code="$(curl --silent --output /dev/null --max-time 2 --request POST --write-out '%{http_code}' "$base_url/jobs/$job_id" || true)"
  [[ "$code" == "202" ]] && break
  sleep 1
done
[[ "$code" == "202" ]] || {
  echo "$phase verification could not enqueue a job: HTTP $code" >&2
  exit 1
}

for _ in $(seq 1 30); do
  body="$(curl --silent --fail --max-time 2 "$base_url/jobs/$job_id" || true)"
  [[ "$body" == "completed" ]] && exit 0
  sleep 1
done
echo "$phase verification timed out waiting for job $job_id" >&2
exit 1
