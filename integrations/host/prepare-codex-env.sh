#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Create a mode-0600 environment file for the Replaybook Codex adapter.

Usage:
  prepare-codex-env.sh [OUTPUT_FILE]

The generated credential contains the current Codex access and ID tokens but
replaces the refresh token. A disposable VM therefore cannot rotate or expose
the refresh credential used by the local Codex login.
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

codex_home="${CODEX_HOME:-${HOME}/.codex}"
auth_file="${codex_home}/auth.json"
[[ -f "$auth_file" ]] || {
  echo "Codex authentication cache does not exist: ${auth_file}" >&2
  exit 1
}
jq -e '
  .tokens.access_token | type == "string" and length > 0
' "$auth_file" >/dev/null || {
  echo "Codex authentication cache has no access token" >&2
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
  output="$(mktemp "${TMPDIR:-/var/tmp}/replaybook-codex-env.XXXXXX")"
fi
chmod 0600 "$output"

auth_b64="$(
  jq -c '.tokens.refresh_token = "replaybook-disabled"' "$auth_file" \
    | base64 --wrap=0
)"
printf 'CODEX_AUTH_JSON_B64=%q\n' "$auth_b64" >"$output"
printf '%s\n' "$output"
