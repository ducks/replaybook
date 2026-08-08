#!/usr/bin/env bash
set -euo pipefail

timeout_seconds="${1:?usage: ssh-probe.sh TIMEOUT_SECONDS COMMAND [ARG ...]}"
shift

[[ "$timeout_seconds" =~ ^[1-9][0-9]*$ ]] || {
  echo "SSH probe timeout must be a positive integer" >&2
  exit 2
}
(( $# > 0 )) || {
  echo "SSH probe command is required" >&2
  exit 2
}

exec timeout --signal=TERM --kill-after=1s "${timeout_seconds}s" "$@"
