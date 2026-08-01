#!/usr/bin/env bash
set -uo pipefail

mkdir -p /logs/verifier

for _ in $(seq 1 10); do
  if [[ "$(curl --silent --output /dev/null --write-out '%{http_code}' http://nginx/health)" == "200" ]]; then
    echo 1 > /logs/verifier/reward.txt
    echo "PASS: nginx health endpoint returned HTTP 200"
    exit 0
  fi
  sleep 1
done

echo 0 > /logs/verifier/reward.txt
echo "FAIL: nginx health endpoint did not return HTTP 200" >&2
exit 1
