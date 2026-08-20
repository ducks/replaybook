#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Create a mode-0600 environment file for the Replaybook OpenCode adapter.

Usage:
  prepare-opencode-env.sh [OUTPUT_FILE]

The generated credential contains only the OpenCode Go API credential from the
local OpenCode authentication cache. Other configured provider credentials are
not copied into the disposable incident VM.
EOF
}

if (( $# > 1 )); then
  usage >&2
  exit 2
fi
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

data_home="${XDG_DATA_HOME:-${HOME}/.local/share}"
auth_file="${data_home}/opencode/auth.json"
[[ -f "$auth_file" ]] || {
  echo "OpenCode authentication cache does not exist: ${auth_file}" >&2
  exit 1
}
jq -e '
  .["opencode-go"].type == "api" and
  (.["opencode-go"].key | type == "string" and length > 0)
' "$auth_file" >/dev/null || {
  echo "OpenCode authentication cache has no OpenCode Go API credential" >&2
  exit 1
}

if [[ -n "${1:-}" ]]; then
  output="$1"
  [[ ! -e "$output" ]] || {
    echo "output file already exists: ${output}" >&2
    exit 1
  }
  umask 077
  : >"$output"
else
  output="$(mktemp "${TMPDIR:-/var/tmp}/replaybook-opencode-env.XXXXXX")"
fi
chmod 0600 "$output"

auth_b64="$(
  jq -c '{"opencode-go": .["opencode-go"]}' "$auth_file" \
    | base64 --wrap=0
)"
printf 'OPENCODE_AUTH_JSON_B64=%q\n' "$auth_b64" >"$output"
printf '%s\n' "$output"
