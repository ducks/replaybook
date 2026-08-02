#!/bin/sh
set -eu

mkdir -p /var/lib/replaybook
if [ ! -e /var/lib/replaybook/fault-injected ]; then
    for _ in $(seq 1 30); do
        canary_ip="$(python -c 'import socket; print(socket.gethostbyname("backend-canary"))' 2>/dev/null || true)"
        [ -n "$canary_ip" ] && break
        sleep 1
    done
    if [ -z "${canary_ip:-}" ]; then
        echo "could not resolve backend-canary" >&2
        exit 1
    fi
    echo "$canary_ip backend # stale pin from network maintenance" >> /etc/hosts
    touch /var/lib/replaybook/fault-injected
fi

exec python /app/app.py
