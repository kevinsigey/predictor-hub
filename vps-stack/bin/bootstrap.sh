#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 VPS. Run as root (or with sudo)
# from /opt/stack after copying this folder there (see README.md,
# "First-time setup"):
#
#   sudo bash /opt/stack/bin/bootstrap.sh /tmp/ci_deploy.pub
#
# The argument is the PUBLIC half of the key GitHub Actions deploys with. It
# is installed for the `deploy` user locked to bin/deploy. Your own keys
# (the ones the provider installed when you created the server) are copied
# to `deploy` with full access.
#
# Safe to re-run: every step checks before it changes anything.
set -euo pipefail

STACK_DIR=/opt/stack
CI_PUBKEY_FILE=${1:?usage: bootstrap.sh <ci-deploy-public-key-file>}
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
[[ -f "$CI_PUBKEY_FILE" ]] || { echo "no such file: $CI_PUBKEY_FILE" >&2; exit 1; }
[[ -f "$STACK_DIR/compose.yml" ]] || { echo "copy the vps-stack folder to $STACK_DIR first" >&2; exit 1; }

echo "==> packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq ca-certificates curl git ufw unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> docker"
if ! command -v docker >/dev/null; then
  . /etc/os-release
  if curl -fsS -o /dev/null "https://download.docker.com/linux/ubuntu/dists/$VERSION_CODENAME/stable/Release"; then
    # Docker's own repo, when it covers this release.
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
      >/etc/apt/sources.list.d/docker.list
    apt-get update -q
    apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  else
    # New releases (e.g. 26.04) come before Docker's repo supports them;
    # Ubuntu's own packages are current enough.
    apt-get install -yq docker.io docker-compose-v2 docker-buildx
  fi
  systemctl enable --now docker
fi
if [[ ! -f /etc/docker/daemon.json ]]; then
  cat >/etc/docker/daemon.json <<'EOF'
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
EOF
  systemctl restart docker
fi

echo "==> 2G swap (headroom for pandas/xgboost spikes on a 4 GB box)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
  echo 'vm.swappiness=10' >/etc/sysctl.d/99-swappiness.conf
  sysctl -q --system
fi

echo "==> deploy user"
id deploy >/dev/null 2>&1 || useradd --create-home --shell /bin/bash deploy
usermod -aG docker deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
# Your own keys come from whoever ran this: the `ubuntu` user via sudo on
# OVH, root on Hetzner. Only bare key lines are kept, which drops the
# `command="echo 'Please login as the user ubuntu'..."` stub some providers
# put in root's authorized_keys.
ADMIN_KEYS=/root/.ssh/authorized_keys
if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]]; then
  ADMIN_KEYS=$(getent passwd "$SUDO_USER" | cut -d: -f6)/.ssh/authorized_keys
fi
grep -qE '^(ssh-|ecdsa-|sk-)' "$ADMIN_KEYS" || { echo "no usable SSH keys in $ADMIN_KEYS" >&2; exit 1; }
{
  # Your own keys: full shell as deploy.
  grep -E '^(ssh-|ecdsa-|sk-)' "$ADMIN_KEYS"
  # CI key: can only run bin/deploy (see the header of that script).
  printf 'command="%s/bin/deploy",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding %s\n' \
    "$STACK_DIR" "$(cat "$CI_PUBKEY_FILE")"
} >/home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys

echo "==> stack directory"
mkdir -p "$STACK_DIR"/{sites,volumes/nba-tracking,volumes/nfl-cache,volumes/cfb-cache}
[[ -f "$STACK_DIR/.env" ]] || cp "$STACK_DIR/.env.example" "$STACK_DIR/.env"
chown -R deploy:deploy "$STACK_DIR"
chmod 600 "$STACK_DIR/.env"
chmod +x "$STACK_DIR"/bin/*

echo "==> firewall (ssh, http, https)"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

echo "==> ssh: keys only"
cat >/etc/ssh/sshd_config.d/10-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
# sshd uses the first value it reads, and 10- sorts before the provider's
# 50-cloud-init.conf (which turns passwords on), so these win.
sshd -t
systemctl reload ssh || systemctl restart ssh

echo "==> trade hub timers (installed, NOT enabled)"
cp "$STACK_DIR"/systemd/tradehub-*.{service,timer} /etc/systemd/system/
cp "$STACK_DIR"/systemd/nba-{rescore,scores}.{service,timer} /etc/systemd/system/   # enabled below: nothing else re-scores NBA upcoming games
cp "$STACK_DIR"/systemd/nba-db-backup.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable nba-rescore.timer nba-scores.timer nba-db-backup.timer

cat <<EOF

Done. Next:
  1. Edit $STACK_DIR/.env (DOMAIN, API keys).
  2. As deploy:  cd $STACK_DIR && docker compose pull && docker compose up -d
  3. Note this host key fingerprint; bin/set-github-secrets.sh asks you to confirm it:
$(ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub)
EOF
