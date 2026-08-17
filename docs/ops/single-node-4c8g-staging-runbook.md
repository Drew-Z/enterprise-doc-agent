# Single-Node 4C8G Staging Runbook

This runbook provisions the reviewed `single-node-4c8g` profile on a clean Ubuntu
24.04 host. It is a production-like staging and recovery-drill environment, not HA
production. PostgreSQL/pgvector, R2, chat/embedding providers, alert delivery and
backups remain external. Prometheus keeps only bounded single-node staging telemetry.

## Safety boundary

- Keep provider console or Tencent TAT access working before changing SSH or UFW.
- Install and enroll Tailscale, then verify key-based OpenSSH over the Tailnet in a second
  terminal before applying the baseline.
- Never allow public `6443/tcp`, `10250/tcp` or `8472/udp`.
- Cloudflare Tunnel routes only the application hostname to loopback Traefik. It never
  routes the Kubernetes API.
- K3s ServiceLB is disabled. Packaged Traefik is a ClusterIP Service with loopback-only
  host ports `127.0.0.1:8080` and `127.0.0.1:8443` for the host tunnel connector.
- Repository scripts do not accept Tailscale auth keys, GitHub runner, Cloudflare,
  database, R2 or model credentials. Those remain explicit operator-owned steps.
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

Only when a temporary public SSH fallback is required, obtain the client address seen by
the initial public SSH session and convert it to a single-host CIDR. Review it before use:

```bash
operator_ip="$(printf '%s\n' "$SSH_CONNECTION" | awk '{print $1}')"
printf 'operator_ip=%s\n' "$operator_ip"
```

Use `<operator-ip>/32` for IPv4 or `<operator-ip>/128` for IPv6. Do not use `0.0.0.0/0`
or `::/0`. A dynamic client address is not a durable operator boundary, so omit this
fallback after Tailnet access and provider console/TAT recovery are both verified.

## Enroll and verify Tailscale

Install Tailscale from its official Ubuntu 24.04 repository while the provider console and
the initial key-based SSH session are still available:

```bash
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg \
  | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list \
  | sudo tee /etc/apt/sources.list.d/tailscale.list >/dev/null
sudo apt-get update
sudo apt-get install -y tailscale
sudo systemctl enable --now tailscaled
sudo tailscale up
tailscale status
tailscale ip -4
```

`tailscale up` is an operator-owned enrollment step. Do not put its auth key in the
repository, command history, script arguments, CI logs or evidence files. Prefer the
interactive login URL; if an ephemeral/pre-authorized key is operationally required,
provide it outside the repository and revoke or expire it immediately after enrollment.

From a second Tailnet-connected terminal, connect to the Tailscale address with the normal
OpenSSH key and keep that session open:

```powershell
ssh enterprise-server
```

On the host, confirm that the active SSH client is a known Tailnet peer:

```bash
client_ip="$(printf '%s\n' "$SSH_CONNECTION" | awk '{print $1}')"
sudo tailscale whois "$client_ip"
ip link show tailscale0
```

## Apply baseline and reboot

After the second Tailnet key-login terminal and provider console are both verified, run
the baseline from that Tailnet SSH session:

```bash
cd /tmp/enterprise-doc-host
sudo bash ./bootstrap-host.sh --apply \
  --confirm-console-access \
  --confirm-key-session \
  --confirm-tailnet-session
sudo reboot
```

Add `--operator-ssh-cidr '<reviewed-single-host-cidr>'` only when a temporary public SSH
fallback is explicitly required. The script validates it as one unicast host and never
accepts a broad network.

The baseline upgrades the OS, disables swap, loads K3s kernel modules, applies bounded
sysctls/journald retention, validates an SSH hardening drop-in, and enables UFW only after
the explicit `tailscale0` SSH allow rule exists. Reconnect over the Tailnet after reboot
and verify:

```bash
sudo sshd -t
sudo ufw status verbose
systemctl is-active tailscaled
tailscale status --peers=false
tailscale ip -4
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
Python 3.12 with `PyYAML==6.0.3`, `cryptography==50.0.0`, and Kustomize `v5.7.1`.
It also installs the reviewed PGDG package
`postgresql-client-17=17.10-1.pgdg24.04+1`; `--check` validates the exact package and
the `pg_dump`/`pg_restore` major version. The runner archive is unpacked for
`gha-staging`, but registration remains a separate repository-scoped token-bearing
operation.

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
  --runner-asset /tmp/actions-runner-linux-x64-2.336.0.tar.gz \
  --postgres-key-asset /tmp/apt.postgresql.org.asc
```

Verify the cluster locally on the node:

```bash
sudo kubectl get nodes -o wide
sudo kubectl get pods -A -o wide
sudo kubectl get svc -n kube-system traefik
sudo ss -lntup
curl --connect-timeout 3 --insecure --head https://127.0.0.1:8443
```

From an external machine, `22`, `80`, `443`, `6443` and `10250` must not be reachable
directly when no temporary public SSH fallback was requested. OpenSSH remains available
through `tailscale0`; UDP `8472` must remain blocked by the provider security group and
UFW. Remove any temporary provider TCP/22 rule after Tailnet and console/TAT recovery have
both passed their post-reboot checks.

## Application profile

`single-node-4c8g` runs two API and two Web replicas for request-distribution drills, one
Worker, one consumer, one ephemeral Redis and one internal Prometheus. Prometheus retains
at most seven days or 4 GB on a 5 GiB `local-path` PVC. It has no Ingress or NodePort and
NetworkPolicy permits it to scrape only API `8000`, Worker `8081` and consumer `8082`.
The profile has no PDB because every Pod still shares one host. Zero-surge rollouts bound
overlap. Application, Prometheus and migration memory limits stay below 6 GiB and requests
stay below 3 GiB/3 CPU, leaving host and K3s headroom.

Set the protected staging Environment before administrator prerequisites are rendered:

```text
STAGING_DEPLOYMENT_PROFILE=single-node-4c8g
STAGING_OBJECT_STORE_CHECKSUM_MODE=readback_sha256
```

Release `v0.1.12` used the deterministic 8-dimensional embedding fixture. Follow
`docs/ops/real-embedding-rollout.md` before claiming semantic RAG: the new route requires
an independently configured embedding endpoint and Secret, a destructive 1024-dimensional
pgvector migration, the restricted embedding rollout Job, and retrieval-quality evidence.

## Cloudflare Tunnel transport

Tunnel enrollment and credentials remain operator-owned. After the named tunnel routes
the staging hostname to `https://127.0.0.1:8443`, install the reviewed non-secret transport
drop-in separately. This host uses HTTP/2 because bounded staging measurements showed
high-loss QUIC connections to the available edge locations. Re-evaluate the choice after
changing the host network or provider; it is not a universal Cloudflare recommendation.

```bash
cd /tmp/enterprise-doc-host
source_path=systemd/cloudflared.service.d/transport.conf
target=/etc/systemd/system/cloudflared.service.d/transport.conf
backup=${target}.enterprise-doc-before-http2

test -f "$source_path"
if sudo test -e "$target" && ! sudo test -e "$backup"; then
  sudo cp -a "$target" "$backup"
fi
sudo install -d -o root -g root -m 0755 "$(dirname "$target")"
sudo install -o root -g root -m 0644 "$source_path" "$target"
sudo systemctl daemon-reload
sudo systemctl restart cloudflared
systemctl is-active cloudflared
sudo journalctl -u cloudflared --since '2 minutes ago' --no-pager \
  | grep 'Registered tunnel connection' \
  | grep 'protocol=http2'
curl --fail --silent http://127.0.0.1:20241/metrics \
  | grep '^cloudflared_tunnel_ha_connections 4$'
curl --fail --silent --show-error https://agent.playlab.eu.cc/health/ready
```

Do not print `systemctl cat cloudflared` or the full process command into logs because a
remotely managed service can contain the tunnel token. If HTTP/2 fails the health or HA
connection checks, restore the previous file when one existed, otherwise remove the new
drop-in, then reload and restart:

```bash
target=/etc/systemd/system/cloudflared.service.d/transport.conf
backup=${target}.enterprise-doc-before-http2
if sudo test -e "$backup"; then
  sudo install -o root -g root -m 0644 "$backup" "$target"
else
  sudo rm -f "$target"
fi
sudo systemctl daemon-reload
sudo systemctl restart cloudflared
systemctl is-active cloudflared
curl --fail --silent --show-error https://agent.playlab.eu.cc/health/ready
```

## Restricted-network image relay import

When the staging node cannot pull an immutable GHCR image directly, dispatch the reviewed
`Relay Staging Images` workflow. Download each OCI archive with an operator-owned temporary
GET URL without printing or retaining the signed URL. Verify the relay receipt and copy the
versioned receiver script from the same reviewed commit to the node.

Run the receiver once without `--confirm`; this validates the archive checksum and every OCI
image index/manifest descriptor but does not call containerd:

```bash
archive=/tmp/enterprise-doc-api.oci.tar
expected_sha256=<sha256-from-enterprise-doc-api-relay-receipt>
relay_id=import-2026-08-17

sudo python3 scripts/import_staging_oci_archive.py \
  --archive "$archive" \
  --expected-sha256 "$expected_sha256" \
  --base-name "$relay_id"
```

Review the planned command and require its base name to be
`docker.io/library/$relay_id`. Then execute and retain the non-secret result:

```bash
sudo python3 scripts/import_staging_oci_archive.py \
  --archive "$archive" \
  --expected-sha256 "$expected_sha256" \
  --base-name "$relay_id" \
  --record-path "/tmp/enterprise-doc-api-${relay_id}-import.json" \
  --confirm
```

Repeat for API, Worker, consumer and Web using the same receipt-bound relay ID. The receiver
uses `k3s ctr --namespace k8s.io images import --all-platforms --digests` and then inspects
`docker.io/library/<relay-id>@sha256:<digest>` for every archive index and manifest. Stop
before deployment if any alias is absent. Do not replace this check with a top-level-only
`ctr images tag`; nested runtime and attestation records must also resolve canonically.

## Remaining operator gates

After host and K3s verification, continue in this order:

1. Apply `infra/k8s/bootstrap` with the administrative kubeconfig.
2. Render and apply administrator-owned `single-node-4c8g` prerequisites, including the
   Prometheus ConfigMap, ClusterIP Service, `local-path` PVC and metric-only NetworkPolicies.
3. Validate application, GHCR pull and TLS Secrets without retaining raw values.
4. Register the repository-scoped `enterprise-doc-staging` runner.
5. Enroll Cloudflare Tunnel, route the application hostname to
   `https://127.0.0.1:8443` with the origin server name set to the staging hostname, then
   apply and verify the reviewed transport drop-in above.
6. Publish and verify signed immutable images.
7. Dispatch staging. Require migration, application and Prometheus rollout, the bounded embedding
   probe/reindex Job, readiness smoke and authenticated smoke to pass in that order.
8. Perform rollback and recovery drills only after the sanitized rollout report and
   release-record hashes have been retained.

Stop at any failed gate. A Ready K3s node alone is not a successful staging deployment.
