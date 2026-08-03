from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "infra" / "host" / "ubuntu-24.04"
RUNBOOK = ROOT / "docs" / "ops" / "single-node-4c8g-staging-runbook.md"


def _script(name: str) -> str:
    return (HOST / name).read_text(encoding="utf-8")


def _bash() -> str | None:
    if os.name == "nt":
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


def test_host_baseline_assets_have_valid_shell_syntax_and_read_only_help() -> None:
    bash = _bash()
    assert bash is not None, "bash is required to validate host automation"
    for name in ("bootstrap-host.sh", "install-k3s.sh", "provision-runner-toolchain.sh"):
        path = HOST / name
        assert path.is_file()
        subprocess.run([bash, "-n", str(path)], check=True, capture_output=True, text=True)
        completed = subprocess.run(
            [bash, str(path), "--help"], check=True, capture_output=True, text=True
        )
        assert "Usage:" in completed.stdout


def test_host_baseline_fails_closed_and_keeps_check_mode_non_mutating() -> None:
    script = _script("bootstrap-host.sh")
    assert "--check" in script
    assert "--apply" in script
    assert "--operator-ssh-cidr" in script
    assert "--confirm-key-session" in script
    assert "a separate SSH public-key login must be confirmed" in script
    assert "--confirm-tailnet-session" in script
    assert "a separate OpenSSH-over-Tailnet key session must be confirmed" in script
    assert 'VERSION_ID" != "24.04"' in script
    assert "MIN_CPU_COUNT=4" in script
    assert "MIN_MEMORY_KIB=7864320" in script

    check_branch = script.split('case "$MODE" in', maxsplit=1)[1].split("apply)", maxsplit=1)[0]
    for mutating_command in ("apt-get", "swapoff", "systemctl restart", "ufw --force enable"):
        assert mutating_command not in check_branch


def test_operator_ssh_cidr_accepts_only_single_unicast_hosts() -> None:
    validator = HOST / "validate_operator_cidr.py"
    assert validator.is_file()
    for value in ("203.0.113.7/32", "2001:db8::7/128"):
        subprocess.run([sys.executable, str(validator), value], check=True, capture_output=True)

    for value in (
        "0.0.0.0/0",
        "::/0",
        "203.0.113.0/24",
        "2001:db8::/64",
        "0.0.0.0/32",
        "ff02::1/128",
    ):
        completed = subprocess.run(
            [sys.executable, str(validator), value], check=False, capture_output=True, text=True
        )
        assert completed.returncode != 0, value


def test_host_baseline_hardens_ssh_and_preserves_k3s_networking() -> None:
    script = _script("bootstrap-host.sh")
    assert "/etc/ssh/sshd_config.d/00-enterprise-doc-hardening.conf" in script
    assert "rm -f /etc/ssh/sshd_config.d/99-enterprise-doc-hardening.conf" in script
    for directive in (
        "PermitRootLogin no",
        "PasswordAuthentication no",
        "KbdInteractiveAuthentication no",
        "X11Forwarding no",
    ):
        assert directive in script
    runtime_dir = "install -d -o root -g root -m 0755 /run/sshd"
    assert runtime_dir in script
    assert "sshd -t" in script
    assert script.index(runtime_dir) < script.index("sshd -t")
    assert script.index("sshd -t") < script.index("systemctl reload ssh")

    assert "ufw default deny incoming" in script
    assert "ufw default allow outgoing" in script
    assert "ufw allow in on tailscale0 to any port 22 proto tcp" in script
    assert 'if test -n "$OPERATOR_SSH_CIDR"; then' in script
    assert 'from "$OPERATOR_SSH_CIDR" to any port 22 proto tcp' in script
    for interface in ("cni0", "flannel.1"):
        assert interface in script
    for cidr in ("10.42.0.0/16", "10.43.0.0/16"):
        assert cidr in script
    assert "for port in 80 443 6443 10250" in script
    assert 'port "$port" proto tcp' in script
    assert "port 8472 proto udp" in script


def test_host_baseline_requires_live_tailnet_ssh_before_resetting_ufw() -> None:
    script = _script("bootstrap-host.sh")
    for contract in (
        "command -v tailscale",
        "systemctl is-active --quiet tailscaled",
        "ip link show tailscale0",
        "tailscale status --peers=false",
        'tailscale whois "$ssh_client_ip"',
        "systemctl enable --now apparmor chrony tailscaled",
    ):
        assert contract in script

    assert "--auth-key" not in script
    assert "tailscale up" not in script
    assert script.rindex("verify_tailnet_access") < script.index("ufw --force reset")
    assert script.index("ufw allow in on tailscale0") < script.index("ufw --force enable")


def test_host_runbook_keeps_tailscale_enrollment_operator_owned() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    for contract in (
        "sudo tailscale up",
        "sudo tailscale whois",
        "--confirm-tailnet-session",
        "ufw status verbose",
        "Remove any temporary provider TCP/22 rule",
    ):
        assert contract in runbook
    assert "Do not put its auth key" in runbook


def test_host_baseline_configures_swap_kernel_and_bounded_logs() -> None:
    script = _script("bootstrap-host.sh")
    assert "swapoff -a" in script
    assert "overlay" in script
    assert "br_netfilter" in script
    assert "nf_conntrack" in script
    assert "net.ipv4.ip_forward = 1" in script
    assert "net.bridge.bridge-nf-call-iptables = 1" in script
    assert "fs.inotify.max_user_instances = 8192" in script
    assert "SystemMaxUse=512M" in script
    assert "RuntimeMaxUse=128M" in script
    assert "apparmor ca-certificates chrony" in script
    assert "systemctl enable --now apparmor chrony" in script


def test_k3s_and_runner_toolchain_versions_are_exact() -> None:
    versions = (HOST / "versions.env").read_text(encoding="utf-8")
    expected = {
        "K3S_VERSION=v1.36.2+k3s1",
        "RUNNER_VERSION=2.336.0",
        "RUNNER_SHA256=04cf0be1aff4c3ec3554466c39124ca250e3effd8873bb7e8d68535aa9505d5d",
        "KUSTOMIZE_VERSION=v5.7.1",
        "PYTHON_MINOR=3.12",
        "PYYAML_VERSION=6.0.3",
        "CRYPTOGRAPHY_VERSION=49.0.0",
    }
    assert expected <= set(versions.splitlines())

    k3s = _script("install-k3s.sh")
    assert 'INSTALL_K3S_VERSION="$K3S_VERSION"' in k3s
    assert "--asset" in k3s
    assert "--docker-io-mirror" in k3s
    assert "--docker-io-mirror must be an HTTPS host" in k3s
    assert "/etc/rancher/k3s/registries.yaml" in k3s
    assert 'wait --for=create pod -l "$label"' in k3s
    assert "disable:\n  - servicelb" in k3s
    assert "type: ClusterIP" in k3s
    assert "hostPort: 8080\n        hostIP: 127.0.0.1" in k3s
    assert "hostPort: 8443\n        hostIP: 127.0.0.1" in k3s
    assert 'wait_traefik_private_service "$timeout"' in k3s
    assert '"--entryPoints.web.address=:8000/tcp"' in k3s
    assert '"--entryPoints.websecure.address=:8443/tcp"' in k3s
    assert "updateStrategy:\n      type: Recreate" in k3s
    assert "check_cluster 300s" in k3s
    assert 'wait_traefik_loopback "$timeout"' in k3s
    assert 'test -f "$ASSET_PATH"' in k3s
    assert '"$K3S_AMD64_SHA256" "$asset_path"' in k3s
    assert 'install -o root -g root -m 0755 "$asset_path" /usr/local/bin/k3s' in k3s
    assert 'write-kubeconfig-mode: "0600"' in k3s
    assert "system-reserved=cpu=300m,memory=512Mi,ephemeral-storage=1Gi" in k3s
    assert "kube-reserved=cpu=300m,memory=512Mi,ephemeral-storage=1Gi" in k3s
    assert "kubectl wait --for=condition=Ready node" in k3s
    assert "traefik" in k3s.lower()
    assert "--disable traefik" not in k3s.lower()

    toolchain = _script("provision-runner-toolchain.sh")
    assert "/opt/enterprise-doc-toolchain/python" in toolchain
    assert "/opt/actions-runner" in toolchain
    assert "useradd" in toolchain
    assert "--kustomize-asset" in toolchain
    assert "--runner-asset" in toolchain
    assert '"$KUSTOMIZE_LINUX_AMD64_SHA256" "$kustomize_asset_path"' in toolchain
    assert '"$RUNNER_SHA256" "$runner_asset_path"' in toolchain
    assert "Runner.Listener --version" in toolchain
    assert "refusing to replace a registered Actions runner" in toolchain
    assert "./config.sh" not in toolchain
    assert "registration-token" not in toolchain
