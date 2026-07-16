#!/usr/bin/env bash
set -euo pipefail

REPO="ducks/replaybook"
BIN="replaybook"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"
VERSION="${REPLAYBOOK_VERSION:-latest}"

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
ARTIFACT="${BIN}-${TARGET}"
if [[ "$VERSION" == "latest" ]]; then
  RELEASE_URL="https://github.com/${REPO}/releases/latest/download"
else
  [[ "$VERSION" == v* ]] || VERSION="v${VERSION}"
  RELEASE_URL="https://github.com/${REPO}/releases/download/${VERSION}"
fi
URL="${RELEASE_URL}/${ARTIFACT}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    echo "no SHA-256 tool found (need sha256sum or shasum)" >&2
    exit 1
  fi
}

echo "Downloading ${BIN} ${VERSION} for ${TARGET}..."
curl -fsSL "$URL" -o "${TMP_DIR}/${ARTIFACT}"
if curl -fsSL "${RELEASE_URL}/SHA256SUMS" -o "${TMP_DIR}/SHA256SUMS"; then
  expected="$(awk -v artifact="$ARTIFACT" '$2 == artifact || $2 == "*" artifact { print $1 }' "${TMP_DIR}/SHA256SUMS")"
  if [[ -z "$expected" ]]; then
    echo "SHA256SUMS does not contain ${ARTIFACT}" >&2
    exit 1
  fi
  actual="$(sha256_file "${TMP_DIR}/${ARTIFACT}")"
  if [[ "$actual" != "$expected" ]]; then
    echo "checksum verification failed for ${ARTIFACT}" >&2
    exit 1
  fi
  echo "Checksum verified."
elif [[ "$VERSION" == "latest" ]]; then
  echo "warning: latest release has no SHA256SUMS; continuing for backward compatibility" >&2
elif [[ "${ALLOW_UNVERIFIED_DOWNLOAD:-0}" != "1" ]]; then
  echo "release has no SHA256SUMS; set ALLOW_UNVERIFIED_DOWNLOAD=1 to install an older release" >&2
  exit 1
fi
chmod +x "${TMP_DIR}/${ARTIFACT}"

if [[ -w "$INSTALL_DIR" ]]; then
  install -m 0755 "${TMP_DIR}/${ARTIFACT}" "${INSTALL_DIR}/${BIN}"
  ln -sf "${INSTALL_DIR}/${BIN}" "${INSTALL_DIR}/replay"
else
  sudo install -m 0755 "${TMP_DIR}/${ARTIFACT}" "${INSTALL_DIR}/${BIN}"
  sudo ln -sf "${INSTALL_DIR}/${BIN}" "${INSTALL_DIR}/replay"
fi

echo "Installed to ${INSTALL_DIR}/${BIN}"
"${INSTALL_DIR}/${BIN}" --version
