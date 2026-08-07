#!/usr/bin/env bash
set -euo pipefail

base_url="${1:?usage: preflight.sh BASE_URL}"
for _ in $(seq 1 20); do
  code="$(curl --silent --output /dev/null --max-time 2 --write-out '%{http_code}' "$base_url/health" || true)"
  [[ "$code" == "502" ]] && exit 0
  sleep 1
done
echo "expected $base_url/health to return HTTP 502, got ${code:-000}" >&2
exit 1
