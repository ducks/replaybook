#!/usr/bin/env bash
set -euo pipefail

base_url="${1:?usage: preflight.sh BASE_URL}"
[[ "$(curl --silent --fail --max-time 2 "$base_url/health")" == "ok" ]]

job_id="preflight-$(date +%s)-$$"
code="$(curl --silent --output /dev/null --max-time 2 --request POST --write-out '%{http_code}' "$base_url/jobs/$job_id")"
[[ "$code" == "202" ]]
sleep 4
code="$(curl --silent --output /dev/null --max-time 2 --write-out '%{http_code}' "$base_url/jobs/$job_id")"
if [[ "$code" != "404" ]]; then
  echo "expected queued job to remain pending, got HTTP $code" >&2
  exit 1
fi
