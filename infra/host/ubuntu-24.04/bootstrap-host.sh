#!/usr/bin/env bash
set -Eeuo pipefail

MIN_CPU_COUNT=4
MIN_MEMORY_KIB=7864320
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE=""
OPERATOR_SSH_CIDR=""
CONSOLE_CONFIRMED=false
KEY_SESSION_CONFIRMED=false
TAILNET_SESSION_CONFIRMED=false

usage() {
  cat <<'EOF'
Usage:
  bootstrap-host.sh --check
  sudo bootstrap-host.sh --apply --confirm-console-access --confirm-key-session \
    --confirm-tailnet-session [--operator-ssh-cidr <IPv4-or-IPv6-host-CIDR>]

--check is read-only. --apply upgrades and hardens a clean Ubuntu 24.04 host.
Run --apply from a verified Tailscale SSH key session while provider console/TAT
access remains available. The optional operator CIDR retains a public SSH fallback.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --check | --apply)
      test -z "$MODE" || die "choose exactly one of --check or --apply"
      MODE="${1#--}"
      shift
      ;;
    --operator-ssh-cidr)
      (($# >= 2)) || die "--operator-ssh-cidr requires a value"
      OPERATOR_SSH_CIDR="$2"
      shift 2
      ;;
    --confirm-console-access)
      CONSOLE_CONFIRMED=true
      shift
      ;;
    --confirm-key-session)
      KEY_SESSION_CONFIRMED=true
      shift
      ;;
    --confirm-tailnet-session)
      TAILNET_SESSION_CONFIRMED=true
      shift
      ;;
    --help | -h)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

test -n "$MODE" || {
  usage >&2
  exit 2
}

read_host_facts() {
  test -r /etc/os-release || die "/etc/os-release is unavailable"
  # shellcheck disable=SC1091
  source /etc/os-release
  test "${ID:-}" = "ubuntu" || die "host must run Ubuntu"
  if test "$VERSION_ID" != "24.04"; then
    die "host must run Ubuntu 24.04"
  fi

  CPU_COUNT="$(nproc)"
  MEMORY_KIB="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
  ((CPU_COUNT >= MIN_CPU_COUNT)) || die "host requires at least 4 CPUs"
  ((MEMORY_KIB >= MIN_MEMORY_KIB)) || die "host requires at least 7.5 GiB RAM"
  PUBLIC_INTERFACE="$(ip route show default | awk 'NR == 1 {print $5}')"
  test -n "$PUBLIC_INTERFACE" || die "default-route interface was not found"
}

validate_operator_cidr() {
  test -n "$OPERATOR_SSH_CIDR" || return 0
  python3 "$SCRIPT_DIR/validate_operator_cidr.py" "$OPERATOR_SSH_CIDR"
}

verify_key_access() {
  local operator_user="${SUDO_USER:-$USER}"
  local operator_home
  operator_home="$(getent passwd "$operator_user" | cut -d: -f6)"
  test -n "$operator_home" || die "operator account was not found"
  test -s "$operator_home/.ssh/authorized_keys" || die "verified SSH authorized_keys is required"
  test -n "${SSH_CONNECTION:-}" || die "apply must run from a verified SSH key session"
}

verify_tailnet_access() {
  local ssh_client_ip

  command -v tailscale >/dev/null 2>&1 \
    || die "Tailscale must be installed and enrolled before applying the baseline"
  systemctl is-active --quiet tailscaled \
    || die "tailscaled must be active before applying the baseline"
  ip link show tailscale0 >/dev/null 2>&1 \
    || die "tailscale0 must exist before applying the baseline"
  tailscale status --peers=false >/dev/null 2>&1 \
    || die "Tailscale must report a healthy local connection"

  ssh_client_ip="${SSH_CONNECTION%% *}"
  tailscale whois "$ssh_client_ip" >/dev/null 2>&1 \
    || die "apply must run through a verified Tailscale peer"
}

case "$MODE" in
  check)
    read_host_facts
    printf 'ubuntu_version=%s\ncpu_count=%s\nmemory_kib=%s\npublic_interface=%s\n' \
      "$VERSION_ID" "$CPU_COUNT" "$MEMORY_KIB" "$PUBLIC_INTERFACE"
    if systemctl is-active --quiet k3s 2>/dev/null; then
      printf 'k3s_state=active\n'
    else
      printf 'k3s_state=absent_or_inactive\n'
    fi
    if command -v tailscale >/dev/null 2>&1 \
      && systemctl is-active --quiet tailscaled 2>/dev/null \
      && ip link show tailscale0 >/dev/null 2>&1; then
      printf 'tailscale_state=active\n'
    else
      printf 'tailscale_state=absent_or_inactive\n'
    fi
    exit 0
    ;;
  apply)
    ;;
  *)
    die "unsupported mode"
    ;;
esac

test "$(id -u)" -eq 0 || die "--apply must run as root"
read_host_facts
validate_operator_cidr
test "$CONSOLE_CONFIRMED" = true || die "provider console/TAT access must be confirmed"
test "$KEY_SESSION_CONFIRMED" = true \
  || die "a separate SSH public-key login must be confirmed"
test "$TAILNET_SESSION_CONFIRMED" = true \
  || die "a separate OpenSSH-over-Tailnet key session must be confirmed"
verify_key_access
verify_tailnet_access
if systemctl is-active --quiet k3s 2>/dev/null; then
  die "refusing to rewrite the host baseline while k3s is active"
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y dist-upgrade
apt-get install -y --no-install-recommends \
  apparmor ca-certificates chrony curl jq openssh-server python3 python3.12 \
  python3.12-venv tar gzip unzip ufw
systemctl enable --now apparmor chrony tailscaled

install -d -m 0755 /etc/modules-load.d /etc/sysctl.d /etc/systemd/journald.conf.d
cat >/etc/modules-load.d/99-enterprise-doc-k3s.conf <<'EOF'
overlay
br_netfilter
nf_conntrack
EOF
modprobe overlay
modprobe br_netfilter
modprobe nf_conntrack

cat >/etc/sysctl.d/99-enterprise-doc-k3s.conf <<'EOF'
net.ipv4.ip_forward = 1
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 1
fs.inotify.max_user_instances = 8192
fs.inotify.max_user_watches = 1048576
EOF
sysctl --system >/dev/null

cat >/etc/systemd/journald.conf.d/99-enterprise-doc-limits.conf <<'EOF'
[Journal]
SystemMaxUse=512M
RuntimeMaxUse=128M
MaxRetentionSec=14day
EOF
systemctl restart systemd-journald

swapoff -a
if test ! -e /etc/fstab.enterprise-doc-before-swap-disable; then
  cp --preserve=all /etc/fstab /etc/fstab.enterprise-doc-before-swap-disable
fi
awk '
  /^[[:space:]]*#/ { print; next }
  NF >= 3 && $3 == "swap" { print "# enterprise-doc disabled swap: " $0; next }
  { print }
' /etc/fstab > /etc/fstab.enterprise-doc-new
install -m 0644 /etc/fstab.enterprise-doc-new /etc/fstab
rm -f /etc/fstab.enterprise-doc-new

install -d -m 0755 /etc/ssh/sshd_config.d
install -d -o root -g root -m 0755 /run/sshd
rm -f /etc/ssh/sshd_config.d/99-enterprise-doc-hardening.conf
cat >/etc/ssh/sshd_config.d/00-enterprise-doc-hardening.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
X11Forwarding no
AllowTcpForwarding no
PermitTunnel no
MaxAuthTries 3
LoginGraceTime 30
EOF
if ! sshd -t; then
  rm -f /etc/ssh/sshd_config.d/00-enterprise-doc-hardening.conf
  sshd -t
  die "SSH hardening failed validation and was removed"
fi

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow in on lo
ufw allow in on tailscale0 to any port 22 proto tcp
if test -n "$OPERATOR_SSH_CIDR"; then
  ufw allow in from "$OPERATOR_SSH_CIDR" to any port 22 proto tcp
fi
ufw allow in on cni0 from 10.42.0.0/16
ufw allow in on cni0 from 10.43.0.0/16
ufw allow in on flannel.1 from 10.42.0.0/16
ufw route allow in on cni0
ufw route allow in on flannel.1
for port in 80 443 6443 10250; do
  ufw deny in on "$PUBLIC_INTERFACE" to any port "$port" proto tcp
done
ufw deny in on "$PUBLIC_INTERFACE" to any port 8472 proto udp
ufw --force enable

systemctl reload ssh
printf 'host baseline applied with Tailnet SSH preserved; reboot before installing k3s\n'
