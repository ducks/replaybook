#!/usr/bin/env bash
set -euo pipefail

REPO="ducks/replaybook"
BIN="replaybook"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"

detect_target() {
  local os arch
  os="$(uname -s)"
  arch="$(uname -m)"

  case "$os" in
    Linux)
      case "$arch" in
        x86_64) echo "linux-x86_64" ;;
        aarch64|arm64) echo "linux-arm64" ;;
        *) echo "unsupported arch: $arch" >&2; exit 1 ;;
      esac
      ;;
    Darwin)
      case "$arch" in
        x86_64) echo "macos-x86_64" ;;
        arm64) echo "macos-arm64" ;;
        *) echo "unsupported arch: $arch" >&2; exit 1 ;;
      esac
      ;;
    *)
      echo "unsupported OS: $os" >&2
      exit 1
      ;;
  esac
}

TARGET="$(detect_target)"
URL="https://github.com/${REPO}/releases/latest/download/${BIN}-${TARGET}"

echo "Downloading ${BIN} for ${TARGET}..."
curl -fsSL "$URL" -o "/tmp/${BIN}"
chmod +x "/tmp/${BIN}"

if [[ -w "$INSTALL_DIR" ]]; then
  mv "/tmp/${BIN}" "${INSTALL_DIR}/${BIN}"
else
  sudo mv "/tmp/${BIN}" "${INSTALL_DIR}/${BIN}"
fi

echo "Installed to ${INSTALL_DIR}/${BIN}"
echo "Run: replaybook list"
