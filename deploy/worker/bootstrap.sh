#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib.sh
source "${SCRIPT_DIR}/../lib.sh"

require_root
require_systemd

TRAINING_USER="replaybook"
CONTROLLER_KEY_FILE="${CONTROLLER_KEY_FILE:?set CONTROLLER_KEY_FILE to the controller public-key file}"
ADMIN_KEY_FILE="${ADMIN_KEY_FILE:?set ADMIN_KEY_FILE to the worker-admin public-key file}"

validate_user "$TRAINING_USER"
[[ -f "$CONTROLLER_KEY_FILE" ]] || die "controller public-key file does not exist"
CONTROLLER_KEY="$(cat "$CONTROLLER_KEY_FILE")"
[[ "$CONTROLLER_KEY" != *$'\n'* ]] || die "controller key file must contain exactly one key"
[[ "$CONTROLLER_KEY" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+($|\ .*) ]] || \
  die "controller key must be an Ed25519 public key"
[[ -f "$ADMIN_KEY_FILE" ]] || die "admin public-key file does not exist"
ADMIN_KEY="$(cat "$ADMIN_KEY_FILE")"
[[ "$ADMIN_KEY" != *$'\n'* ]] || die "admin key file must contain exactly one key"
[[ "$ADMIN_KEY" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+($|\ .*) ]] || \
  die "admin key must be an Ed25519 public key"

# The worker bootstrap deliberately targets the fresh Ubuntu images offered by
# Fornex. Refuse other distributions instead of partially configuring a host.
# shellcheck source=/dev/null
source /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || die "worker bootstrap currently supports Ubuntu only"
case "${VERSION_ID:-}" in
  22.04 | 24.04 | 26.04) ;;
  *) die "unsupported Ubuntu release: ${VERSION_ID:-unknown}" ;;
esac

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl openssh-server tar

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  apt-get remove -y docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc || true
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
docker compose version >/dev/null || die "Docker Compose plugin is unavailable"
if [[ ! -e /etc/docker/daemon.json ]]; then
  install -d -m 0755 /etc/docker
  cat >/etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF
fi
systemctl enable --now docker.service ssh.service
systemctl restart docker.service

install_replaybook

if ! getent group "$TRAINING_USER" >/dev/null 2>&1; then
  groupadd "$TRAINING_USER"
fi
if ! id "$TRAINING_USER" >/dev/null 2>&1; then
  useradd --gid "$TRAINING_USER" --create-home --shell /bin/bash "$TRAINING_USER"
fi
usermod -aG docker "$TRAINING_USER"
HOME_DIR="$(getent passwd "$TRAINING_USER" | cut -d: -f6)"
install -d -m 0700 -o "$TRAINING_USER" -g "$TRAINING_USER" "$HOME_DIR/.ssh"
printf 'restrict %s\n' "$CONTROLLER_KEY" >"$HOME_DIR/.ssh/authorized_keys"
chown "$TRAINING_USER:$TRAINING_USER" "$HOME_DIR/.ssh/authorized_keys"
chmod 0600 "$HOME_DIR/.ssh/authorized_keys"

install -d -m 0700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 0600 /root/.ssh/authorized_keys
if ! grep -qxF "$ADMIN_KEY" /root/.ssh/authorized_keys; then
  printf '%s\n' "$ADMIN_KEY" >>/root/.ssh/authorized_keys
fi

cat >/etc/tmpfiles.d/replaybook-worker.conf <<EOF
d /tmp/replaybook-hosted 0700 $TRAINING_USER $TRAINING_USER -
EOF
systemd-tmpfiles --create /etc/tmpfiles.d/replaybook-worker.conf

rm -f /etc/ssh/sshd_config.d/99-replaybook-worker.conf
cat >/etc/ssh/sshd_config.d/00-replaybook-worker.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
AllowAgentForwarding no
AllowTcpForwarding no
X11Forwarding no
PermitTunnel no
MaxAuthTries 3
EOF
sshd -t
EFFECTIVE_SSH_CONFIG="$(sshd -T)"
grep -qx 'passwordauthentication no' <<<"$EFFECTIVE_SSH_CONFIG" || \
  die "effective SSH configuration still allows passwords"
grep -qx 'kbdinteractiveauthentication no' <<<"$EFFECTIVE_SSH_CONFIG" || \
  die "effective SSH configuration still allows keyboard-interactive authentication"
grep -qx 'allowtcpforwarding no' <<<"$EFFECTIVE_SSH_CONFIG" || \
  die "effective SSH configuration still allows TCP forwarding"
systemctl reload ssh.service

runuser -u "$TRAINING_USER" -- docker info >/dev/null

echo
echo "Worker ready. Verify this host-key fingerprint on the controller:"
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256
echo
echo "Security reminder: this VM is trainee-owned while a session is active."
echo "Do not install provider credentials, controller secrets, or unrelated services here."
