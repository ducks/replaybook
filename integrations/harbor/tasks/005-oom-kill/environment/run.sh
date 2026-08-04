#!/bin/sh

while true; do
    python /app/app.py
    status=$?
    echo "[supervisor] app exited (${status}), restarting in 2s..." >&2
    sleep 2
done
