#!/bin/sh

while true; do
    response="$(
        redis-cli --no-auth-warning --raw -u "$REDIS_URL" \
            SET replaybook:worker-heartbeat "$(date +%s)" 2>/dev/null
    )"
    if [ "$response" = "OK" ]; then
        echo "sidekiq: redis connection healthy"
    else
        echo "sidekiq: redis connection failed: WRONGPASS invalid username-password pair" >&2
    fi
    sleep 2
done
