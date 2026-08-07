#!/usr/bin/env bash
set -euo pipefail

migration="/var/lib/checkout/current/db/migrate/202608070001_add_delivery_state.sql"
[[ -f "$migration" ]]

psql \
  --host 127.0.0.1 \
  --username replaybook \
  --dbname replaybook \
  --set ON_ERROR_STOP=1 \
  --file "$migration"

systemctl restart checkout-sidekiq.service
