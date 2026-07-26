#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this hardening script through sudo." >&2
  exit 1
fi

if [[ ! -r /etc/os-release ]]; then
  echo "Cannot identify the host operating system." >&2
  exit 1
fi

# shellcheck source=/dev/null
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "This hardening script supports Ubuntu hosts only." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  auditd \
  ca-certificates \
  unattended-upgrades \
  ufw

cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

cat >/etc/apt/apt.conf.d/52vv-unattended-upgrades <<'EOF'
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-New-Unused-Dependencies "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
EOF

install -d -m 0755 /etc/ssh/sshd_config.d
cat >/etc/ssh/sshd_config.d/60-vv-hardening.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
PermitEmptyPasswords no
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
PermitTunnel no
MaxAuthTries 3
LoginGraceTime 30
ClientAliveInterval 300
ClientAliveCountMax 2
EOF

sshd -t
systemctl reload ssh

cat >/etc/sysctl.d/60-vv-network-hardening.conf <<'EOF'
kernel.dmesg_restrict = 1
kernel.kptr_restrict = 2
kernel.unprivileged_bpf_disabled = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv4.conf.all.log_martians = 1
net.ipv4.conf.default.log_martians = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.tcp_syncookies = 1
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv6.conf.default.accept_source_route = 0
EOF
sysctl --system >/dev/null

ufw default deny incoming
ufw default allow outgoing
ufw allow from 35.235.240.0/20 to any port 22 proto tcp comment "Google IAP SSH"
ufw allow 80/tcp comment "HTTP certificate redirect"
ufw allow 443/tcp comment "HTTPS"
ufw allow 443/udp comment "HTTP3"
ufw --force enable

install -d -m 0755 /etc/docker
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

deploy_user="${SUDO_USER:-}"
if [[ -n "$deploy_user" && "$deploy_user" != "root" ]]; then
  gpasswd -d "$deploy_user" docker >/dev/null 2>&1 || true
fi
docker_members="$(getent group docker | cut -d: -f4)"
IFS=',' read -r -a docker_member_list <<<"$docker_members"
for docker_member in "${docker_member_list[@]}"; do
  if [[ -n "$docker_member" ]]; then
    gpasswd -d "$docker_member" docker >/dev/null 2>&1 || true
  fi
done

install -d -m 0750 -o root -g root \
  /opt/vv-edgent \
  /opt/vv-edgent/releases \
  /opt/vv-edgent/shared

if [[ -f /opt/vv-edgent/shared/.env ]]; then
  chown root:root /opt/vv-edgent/shared/.env
  chmod 0600 /opt/vv-edgent/shared/.env
fi

systemctl enable --now auditd
systemctl enable unattended-upgrades
systemctl restart docker

echo "Host hardening applied. Verify IAP SSH and application health before ending the session."
