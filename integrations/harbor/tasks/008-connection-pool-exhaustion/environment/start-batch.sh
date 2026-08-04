#!/bin/sh
set -eu

mkdir -p /var/lib/replaybook
if [ ! -e /var/lib/replaybook/fault-injected ]; then
    touch /var/lib/replaybook/fault-injected
    python /app/leak.py >/tmp/leak.log 2>&1 &
fi

exec sleep infinity
