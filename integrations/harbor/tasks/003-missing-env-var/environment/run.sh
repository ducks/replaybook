#!/bin/sh
while true; do
  sh -c '. /app/.env 2>/dev/null; exec python /app/app.py'
  echo "[supervisor] app exited ($?), restarting in 2s..." >&2
  sleep 2
done
