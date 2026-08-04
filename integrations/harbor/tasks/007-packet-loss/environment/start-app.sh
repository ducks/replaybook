#!/bin/sh
set -eu

mkdir -p /var/lib/replaybook
if [ ! -e /var/lib/replaybook/fault-injected ]; then
    tc qdisc add dev eth0 root netem loss 80%
    touch /var/lib/replaybook/fault-injected
fi

exec python /app/app.py
