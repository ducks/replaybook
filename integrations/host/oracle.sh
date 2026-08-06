#!/usr/bin/env bash
set -euo pipefail

sed -i 's/127\.0\.0\.1:3001/127.0.0.1:3000/' /etc/replaybook/nginx.conf
systemctl restart incident-nginx.service
