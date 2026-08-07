#!/usr/bin/env bash
set -euo pipefail

sed -i 's|redis://127.0.0.1:6379/1|redis://127.0.0.1:6379/0|' /etc/replaybook/sidekiq.env
systemctl restart checkout-sidekiq.service
