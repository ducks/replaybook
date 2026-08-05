#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/tasks/012-retry-storm/tests/topology.sh"

getent() {
  [[ "$1" == "hosts" ]] || return 2
  [[ "$2" != "app" ]]
}

unresolved="$(first_unresolved_service primary app fallback)"
[[ "$unresolved" == "app" ]]

getent() {
  [[ "$1" == "hosts" ]] || return 2
  return 0
}

if first_unresolved_service app primary fallback >/dev/null; then
  echo "all-resolvable topology was classified as changed" >&2
  exit 1
fi
