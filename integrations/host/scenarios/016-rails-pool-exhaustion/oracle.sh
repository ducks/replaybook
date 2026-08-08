#!/usr/bin/env bash
set -euo pipefail

sed -i 's/^DB_POOL=.*/DB_POOL=4/' /etc/replaybook/rails.env
systemctl restart checkout-web.service
