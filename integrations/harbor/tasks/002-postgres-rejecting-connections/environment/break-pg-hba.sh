#!/bin/sh
set -eu

# The init hook runs after PostgreSQL creates its default pg_hba.conf and while
# the temporary init server is available for a reload.
sed -i '1i host all all all reject' "$PGDATA/pg_hba.conf"
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -c 'SELECT pg_reload_conf()' >/dev/null
