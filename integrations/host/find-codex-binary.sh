#!/usr/bin/env bash
set -euo pipefail

launcher="$(command -v codex || true)"
[[ -n "$launcher" ]] || {
  echo "codex is not installed" >&2
  exit 1
}
launcher="$(readlink -f "$launcher")"

if [[ "$(basename "$launcher")" == "volta-shim" ]]; then
  volta="$(command -v volta || true)"
  [[ -n "$volta" ]] || {
    echo "Codex uses a Volta shim but the volta command is unavailable" >&2
    exit 1
  }
  launcher="$($volta which codex)"
  launcher="$(readlink -f "$launcher")"
fi

if file -b "$launcher" | grep -q 'ELF'; then
  printf '%s\n' "$launcher"
  exit 0
fi

package_root="$(cd "$(dirname "$launcher")/.." && pwd)"
architecture="$(uname -m)"
case "$architecture" in
  x86_64) target='x86_64-unknown-linux' ;;
  aarch64|arm64) target='aarch64-unknown-linux' ;;
  *)
    echo "unsupported Codex host architecture: ${architecture}" >&2
    exit 1
    ;;
esac

binary="$(
  find "$package_root/node_modules/@openai" \
    -type f -path "*/vendor/${target}*/bin/codex" -perm -0100 \
    -print -quit 2>/dev/null || true
)"
[[ -n "$binary" ]] || {
  echo "native Codex binary was not found under ${package_root}" >&2
  exit 1
}
printf '%s\n' "$binary"
