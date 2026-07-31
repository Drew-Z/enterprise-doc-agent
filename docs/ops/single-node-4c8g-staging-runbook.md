# Single-Node 4C8G Staging Runbook

This runbook provisions the reviewed `single-node-4c8g` profile on a clean Ubuntu
24.04 host. It is a production-like staging and recovery-drill environment, not HA
production. PostgreSQL/pgvector, R2, chat/embedding providers, retained telemetry and
backups remain external.

## Safety boundary

- Keep provider console or Tencent TAT access working before changing SSH or UFW.
- Verify key-based SSH in a second terminal before applying the baseline.
- Never allow public `6443/tcp`, `10250/tcp` or `8472/udp`.
- Cloudflare Tunnel routes only the application hostname to loopback Traefik. It never
  routes the Kubernetes API.
- K3s ServiceLB is disabled. Packaged Traefik is a ClusterIP Service with loopback-only
  host ports `127.0.0.1:8080` and `127.0.0.1:8443` for the host tunnel connector.
- Repository scripts do not accept GitHub runner, Cloudflare, database, R2 or model
  credentials. Those remain explicit operator-owned steps.
- Do not apply the baseline over an active K3s installation. The script fails closed in
  that state.

## Local preflight

From the reviewed repository commit, validate the exact assets before copying them:

```powershell
uv run pytest tests/deployment/test_staging_host_baseline.py -q
uv run pytest tests/deployment/test_m6_contracts.py -q
kubectl kustomize infra/k8s/overlays/single-node-4c8g | Out-Null
docker run --rm -v "${PWD}:/repo" -w /repo rhysd/actionlint:1.7.7
```

Copy only the non-secret host assets to the server:

```powershell
scp -r infra/host/ubuntu-24.04 enterprise-server:/tmp/enterprise-doc-host
```

## Read-only host audit

Run the check before any mutation:

```bash
ssh enterprise-server
cd /tmp/enterprise-doc-host
bash ./bootstrap-host.sh --check
```

The command must report Ubuntu `24.04`, at least four CPUs, at least 7.5 GiB RAM and the
expected public interface. Record only sanitized facts; do not capture environment files,
shell history, authorized keys or provider tokens.

Obtain the client address seen by the active SSH session and convert it to a single-host
CIDR. Review it before use:

```bash
operator_ip="$(printf '%s\n' "$SSH_CONNECTION" | awk '{print $1}')"
printf 'operator_ip=%s\n' "$operator_ip"
```

Use `<operator-ip>/32` for IPv4 or `<operator-ip>/128` for IPv6. Do not use `0.0.0.0/0`
or `::/0`. Restrict the provider security group to the same CIDR before or immediately
after host UFW validation, while retaining TAT/console recovery.

## Apply baseline and reboot

After the second key-login terminal and provider console are both verified:

```bash
cd /tmp/enterprise-doc-host
sudo bash ./bootstrap-host.sh --apply \
  --operator-ssh-cidr '<reviewed-single-host-cidr>' \
  --confirm-console-access \
  --confirm-key-session
sudo reboot
```

The baseline upgrades the OS, disables swap, loads K3s kernel modules, applies bounded
sysctls/journald retention, validates an SSH hardening drop-in, and enables UFW only after
the explicit SSH allow rule exists. Reconnect after reboot and verify:

```bash
sudo sshd -t
sudo ufw status verbose
swapon --show
sysctl net.ipv4.ip_forward net.bridge.bridge-nf-call-iptables
systemctl is-active chrony apparmor
systemctl is-active tat_agent || true
```

`swapon --show` must be empty. `chrony` and AppArmor are installed and enabled by the
baseline. Tencent TAT remains enabled when it is supplied by the provider image; an active
provider console is still required when TAT is absent.

## Install exact K3s and toolchain

Install K3s only after the rebooted baseline passes:

```bash
cd /tmp/enterprise-doc-host
sudo bash ./install-k3s.sh --apply
sudo bash ./install-k3s.sh --check
sudo bash ./provision-runner-toolchain.sh --apply
sudo bash ./provision-runner-toolchain.sh --check
```

The scripts verify SHA-256 for the K3s, Kustomize and Actions runner artifacts. K3s is
fixed to `v1.36.2+k3s1`; packaged Traefik remains enabled. The deployment toolchain is
Python 3.12 with `PyYAML==6.0.3`, `cryptography==49.0.0`, and Kustomize `v5.7.1`.
The runner archive is unpacked for `gha-staging`, but registration remains a separate
repository-scoped token-bearing operation.

If GitHub Releases is too slow from the host, transfer the reviewed binary out of band and
run `sudo bash ./install-k3s.sh --apply --asset /tmp/k3s`. The script still rejects any
asset whose SHA-256 does not match `versions.env`.

For hosts that cannot reach Docker Hub reliably, pass an operator-reviewed HTTPS pull-through
cache explicitly, for example `--docker-io-mirror https://registry-mirror.example.com`. The
script writes K3s `registries.yaml`, restarts K3s only when that configuration changes, and
still requires the system Pods to become Ready.

The same restricted-network path is available for the deployment toolchain:

```bash
sudo bash ./provision-runner-toolchain.sh --apply \
  --kustomize-asset /tmp/kustomize_v5.7.1_linux_amd64.tar.gz \
  --runner-asset /tmp/actions-runner-linux-x64-2.336.0.tar.gz
```

Verify the cluster locally on the node:

```bash
sudo kubectl get nodes -o wide
sudo kubectl get pods -A -o wide
sudo kubectl get svc -n kube-system traefik
sudo ss -lntup
curl --connect-timeout 3 --insecure --head https://127.0.0.1:8443
```

From an external machine, `80`, `443`, `6443` and `10250` must not be reachable directly.
Only SSH from the approved operator CIDR remains public. UDP `8472` must also remain
blocked by the provider security group and UFW.

## Application profile

`single-node-4c8g` runs two API and two Web replicas for request-distribution drills, one
Worker, one consumer and one ephemeral Redis. It has no PDB because every Pod still shares
one host. Zero-surge rollouts bound overlap. Application plus migration memory limits stay
below 6 GiB and requests stay below 3 GiB/3 CPU, leaving host and K3s headroom.

Set the protected staging Environment before administrator prerequisites are rendered:

```text
STAGING_DEPLOYMENT_PROFILE=single-node-4c8g
STAGING_OBJECT_STORE_CHECKSUM_MODE=readback_sha256
```

The initial rollout uses a real OpenAI-compatible chat route but still uses the current
deterministic 8-dimensional embedding implementation. It is valid platform and Agent
orchestration evidence, not evidence of a production embedding migration.

## Remaining operator gates

After host and K3s verification, continue in this order:

1. Apply `infra/k8s/bootstrap` with the administrative kubeconfig.
2. Render and apply administrator-owned `single-node-4c8g` prerequisites.
3. Validate application, GHCR pull and TLS Secrets without retaining raw values.
4. Register the repository-scoped `enterprise-doc-staging` runner.
5. Enroll Cloudflare Tunnel and route the application hostname to
   `https://127.0.0.1:8443`, with the origin server name set to the staging hostname.
6. Publish and verify signed immutable images.
7. Dispatch staging, run authenticated smoke, then perform rollback and recovery drills.

Stop at any failed gate. A Ready K3s node alone is not a successful staging deployment.
