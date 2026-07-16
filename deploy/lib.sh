#!/usr/bin/env bash

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${DEPLOY_DIR}/.." && pwd)"

die() {
  echo "replaybook deploy: $*" >&2
  exit 1
}

require_root() {
  [[ "${EUID}" -eq 0 ]] || die "run this script as root"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

require_systemd() {
  require_command systemctl
  [[ -d /run/systemd/system ]] || die "this deployment kit requires systemd"
}

validate_user() {
  [[ "$1" =~ ^[a-z_][a-z0-9_-]{0,30}$ ]] || die "invalid Linux user: $1"
}

validate_host() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || die "invalid DNS name or IPv4 address: $1"
}

validate_port() {
  local port="$1"
  [[ "$port" =~ ^[0-9]+$ ]] || die "invalid port: $port"
  ((10#$port >= 1 && 10#$port <= 65535)) || die "invalid port: $port"
}

install_replaybook() {
  : "${REPLAYBOOK_VERSION:?set REPLAYBOOK_VERSION to a release such as v20260716.0.2}"
  REPLAYBOOK_VERSION="${REPLAYBOOK_VERSION}" INSTALL_DIR=/usr/local/bin \
    "${REPO_ROOT}/install.sh"
}
