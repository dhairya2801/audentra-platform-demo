#!/usr/bin/env bash
set -euo pipefail

if [[ ! -r /etc/os-release ]]; then
  echo "Cannot identify the host operating system." >&2
  exit 1
fi

# shellcheck source=/dev/null
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "This provisioner supports Ubuntu hosts only." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  auditd \
  ca-certificates \
  curl \
  gnupg \
  unattended-upgrades \
  ufw

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
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
apt-get install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

install -m 0755 -d /etc/docker
cat >/etc/docker/daemon.json <<'EOF'
{
  "live-restore": true,
  "no-new-privileges": true,
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF

systemctl enable docker
systemctl restart docker

deploy_user="${SUDO_USER:-}"
if [[ -z "$deploy_user" || "$deploy_user" == "root" ]]; then
  echo "Run this script through sudo from the deployment user." >&2
  exit 1
fi

# Docker's control socket is root-equivalent. Deployments run through the
# audited root-owned release command rather than granting a login user access
# to the docker group.
gpasswd -d "$deploy_user" docker >/dev/null 2>&1 || true
install -d -m 0750 -o root -g root \
  /opt/vv-edgent \
  /opt/vv-edgent/releases \
  /opt/vv-edgent/shared

docker --version
docker compose version
