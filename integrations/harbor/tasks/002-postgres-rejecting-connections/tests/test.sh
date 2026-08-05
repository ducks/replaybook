#!/usr/bin/env bash
set -uo pipefail

mkdir -p /logs/verifier

fail() {
  printf '%s\n' "$1" > /logs/verifier/failure-category.txt
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL[$1]: $2" >&2
  exit 1
}

db_container="$({
  docker ps \
    --filter label=com.docker.compose.service=db \
    --format '{{.ID}}'
} | head -n 1)"

if [[ -z "$db_container" ]]; then
  fail topology_changed "database container not found"
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
  fail repair_incomplete "PostgreSQL still rejects TCP connections"
fi

# A reload can hide an in-memory-only repair. Restarting the database verifies
# that pg_hba.conf itself is fixed and that the connection survives a reboot.
if ! docker restart "$db_container" >/dev/null; then
  fail restart_failed "database restart failed"
fi

for _ in $(seq 1 15); do
  check_connection && break
  sleep 1
done

if ! check_connection; then
  fail repair_not_durable "PostgreSQL connection did not survive restart"
fi

echo 1 > /logs/verifier/reward.txt
echo "PASS: PostgreSQL accepted TCP connections before and after restart"
