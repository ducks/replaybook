#!/usr/bin/env bash
set -uo pipefail

mkdir -p /logs/verifier

db_container="$({
  docker ps \
    --filter label=com.docker.compose.service=db \
    --format '{{.ID}}'
} | head -n 1)"

if [[ -z "$db_container" ]]; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: database container not found" >&2
  exit 1
fi

check_connection() {
  docker exec -e PGPASSWORD=password "$db_container" \
    psql -h 127.0.0.1 -U postgres -d appdb -c 'SELECT 1' >/dev/null 2>&1
}

for _ in $(seq 1 15); do
  check_connection && break
  sleep 1
done

if ! check_connection; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: PostgreSQL still rejects TCP connections" >&2
  exit 1
fi

# A reload can hide an in-memory-only repair. Restarting the database verifies
# that pg_hba.conf itself is fixed and that the connection survives a reboot.
if ! docker restart "$db_container" >/dev/null; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: database restart failed" >&2
  exit 1
fi

for _ in $(seq 1 15); do
  check_connection && break
  sleep 1
done

if ! check_connection; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: PostgreSQL connection did not survive restart" >&2
  exit 1
fi

echo 1 > /logs/verifier/reward.txt
echo "PASS: PostgreSQL accepted TCP connections before and after restart"
