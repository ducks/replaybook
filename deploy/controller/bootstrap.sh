#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib.sh
source "${SCRIPT_DIR}/../lib.sh"

require_root
require_systemd

CONTROL_USER="replaybook-control"
WORKER_USER="replaybook"
WORKER_HOST="${WORKER_HOST:?set WORKER_HOST, for example training.replaybook.dev}"
WORKER_SSH_PORT="${WORKER_SSH_PORT:-22}"
CONTROL_BIND="${CONTROL_BIND:-127.0.0.1:8080}"
DEFAULT_TTL="${DEFAULT_TTL:-60}"
SCENARIO_REPO="${SCENARIO_REPO:-ducks/replaybook-scenarios}"
STATE_DIR="/var/lib/replaybook"
CONFIG_DIR="/etc/replaybook"

validate_user "$CONTROL_USER"
validate_user "$WORKER_USER"
validate_host "$WORKER_HOST"
validate_port "$WORKER_SSH_PORT"
[[ "$CONTROL_BIND" =~ ^127\.0\.0\.1:([0-9]+)$ ]] || die "CONTROL_BIND must be an IPv4 loopback socket"
validate_port "${BASH_REMATCH[1]}"
[[ "$DEFAULT_TTL" =~ ^[0-9]+$ ]] || die "DEFAULT_TTL must be positive"
((10#$DEFAULT_TTL >= 1 && 10#$DEFAULT_TTL <= 1440)) || die "DEFAULT_TTL must be between 1 and 1440"
[[ "$SCENARIO_REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "invalid SCENARIO_REPO"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git openssh-client openssl
install_replaybook

if ! getent group "$CONTROL_USER" >/dev/null 2>&1; then
  groupadd --system "$CONTROL_USER"
fi
if ! id "$CONTROL_USER" >/dev/null 2>&1; then
  useradd --system --gid "$CONTROL_USER" --create-home --home-dir "$STATE_DIR" \
    --shell /usr/sbin/nologin "$CONTROL_USER"
fi
install -d -m 0750 -o "$CONTROL_USER" -g "$CONTROL_USER" "$STATE_DIR"
install -d -m 0700 -o "$CONTROL_USER" -g "$CONTROL_USER" "$STATE_DIR/.ssh"
install -d -m 0750 -o "$CONTROL_USER" -g "$CONTROL_USER" "$STATE_DIR/data"
install -d -m 0750 -o root -g "$CONTROL_USER" "$CONFIG_DIR"

KEY_PATH="$STATE_DIR/.ssh/id_ed25519"
if [[ ! -f "$KEY_PATH" ]]; then
  runuser -u "$CONTROL_USER" -- ssh-keygen -q -t ed25519 -N "" \
    -C "replaybook-controller" -f "$KEY_PATH"
fi

ADMIN_KEY_PATH="/root/.ssh/replaybook-worker-admin"
install -d -m 0700 /root/.ssh
if [[ ! -f "$ADMIN_KEY_PATH" ]]; then
  ssh-keygen -q -t ed25519 -N "" -C "replaybook-worker-admin" -f "$ADMIN_KEY_PATH"
fi

ENV_FILE="$CONFIG_DIR/control.env"
if [[ -f "$ENV_FILE" ]]; then
  TOKEN="$(sed -n 's/^REPLAYBOOK_CONTROL_TOKEN=//p' "$ENV_FILE")"
  [[ "$TOKEN" =~ ^[A-Fa-f0-9]{64}$ ]] || die "existing control token is malformed"
else
  TOKEN="$(openssl rand -hex 32)"
fi
cat >"$ENV_FILE" <<EOF
REPLAYBOOK_CONTROL_TOKEN=$TOKEN
WORKER_SSH_TARGET=$WORKER_USER@$WORKER_HOST
WORKER_SSH_PORT=$WORKER_SSH_PORT
CONTROL_BIND=$CONTROL_BIND
DEFAULT_TTL=$DEFAULT_TTL
HOME=$STATE_DIR
XDG_DATA_HOME=$STATE_DIR/data
EOF
chmod 0640 "$ENV_FILE"
chown root:"$CONTROL_USER" "$ENV_FILE"

install -m 0644 "${SCRIPT_DIR}/replaybook-control.service" \
  /etc/systemd/system/replaybook-control.service

SCENARIOS_DIR="$STATE_DIR/data/replaybook/scenarios"
PACK_DIR="$SCENARIOS_DIR/${SCENARIO_REPO##*/}"
install -d -m 0750 -o "$CONTROL_USER" -g "$CONTROL_USER" "$SCENARIOS_DIR"
if [[ -d "$PACK_DIR/.git" ]]; then
  runuser -u "$CONTROL_USER" -- git -C "$PACK_DIR" pull --ff-only
elif [[ -e "$PACK_DIR" ]]; then
  die "scenario destination exists but is not a Git checkout: $PACK_DIR"
else
  runuser -u "$CONTROL_USER" -- git clone --depth=1 \
    "https://github.com/${SCENARIO_REPO}.git" "$PACK_DIR"
fi

systemctl daemon-reload
systemctl enable replaybook-control.service

echo
echo "Controller prepared. Install this public key on the worker:"
cat "$KEY_PATH.pub"
echo
echo "Install this separate administrative public key for root access:"
cat "$ADMIN_KEY_PATH.pub"
echo
echo "Then trust the worker host key and start the service with:"
echo "  sudo ${SCRIPT_DIR}/trust-worker.sh ${WORKER_HOST} SHA256:<fingerprint>"
echo
echo "The API bearer token is stored in ${ENV_FILE}."
