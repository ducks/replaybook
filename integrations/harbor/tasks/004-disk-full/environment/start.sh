#!/bin/sh
set -eu

mkdir -p /var/lib/replaybook
if [ ! -e /var/lib/replaybook/fault-injected ]; then
    rm -f /tmp/healthcheck
    dd if=/dev/zero of=/tmp/app-debug.core bs=1M count=16 2>/dev/null || true
    touch /var/lib/replaybook/fault-injected
fi

exec python /app/app.py
