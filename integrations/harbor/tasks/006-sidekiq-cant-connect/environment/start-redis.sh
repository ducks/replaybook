#!/bin/sh
set -eu

mkdir -p /var/lib/replaybook
if [ ! -e /var/lib/replaybook/fault-injected ]; then
    touch /var/lib/replaybook/fault-injected
    password=hunter2
else
    password=correctpassword
fi

exec redis-server --requirepass "$password"
