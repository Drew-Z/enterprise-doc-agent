# Single-Node 4C4G Staging Runbook

This runbook is the deployment target for the Guangzhou Ubuntu 24.04 host with
4 vCPU and 4 GiB RAM (3.8 GiB visible to the guest). It is bounded staging and
demo infrastructure, not HA or production capacity evidence.

## Resource contract

- One replica each of API, Worker, Consumer and Web.
- Redis is ephemeral, capped at 128 MiB, and is not a durable data store.
- PostgreSQL/pgvector, object storage, chat model and embedding provider remain external.
- Prometheus is omitted by default; inspect metrics through a short-lived operator
  port-forward when needed.
- Migration and embedding rollout jobs run with application replicas scaled to zero.
- Zero-surge rollouts prevent temporary replica overlap on the single node.

## Host preparation

Run the host check from a Tailnet SSH session:

```bash
cd /tmp/enterprise-doc-host
bash ./bootstrap-host.sh --check --profile single-node-4c4g
```

After provider console access, the public key session and a second Tailnet SSH session
are verified, apply the baseline:

```bash
sudo bash ./bootstrap-host.sh --apply \
  --profile single-node-4c4g \
  --confirm-console-access \
  --confirm-key-session \
  --confirm-tailnet-session
sudo reboot
```

Reconnect through Tailnet after reboot and verify that Swap is empty, UFW allows SSH only
on `tailscale0`, and `tailscaled`, AppArmor and chrony are active.

## K3s

```bash
sudo bash ./install-k3s.sh --apply --profile single-node-4c4g
sudo bash ./install-k3s.sh --check --profile single-node-4c4g
sudo kubectl get nodes -o wide
sudo kubectl get pods -A -o wide
```

K3s is pinned to reviewed `v1.36.2+k3s1`. Packaged Traefik remains enabled, but its
host ports are loopback-only; do not expose `6443`, `10250` or UDP `8472` publicly.

## Application deployment

Set the protected staging variable:

```text
STAGING_DEPLOYMENT_PROFILE=single-node-4c4g
```

Render and inspect the exact overlay before applying administrator-owned prerequisites:

```powershell
kubectl kustomize infra/k8s/overlays/single-node-4c4g | Out-File staging-4c4g.yaml
```

The workflow must use immutable image digests and external dependency Secrets. A Ready
K3s node alone is not a successful staging deployment; migration, rollout, embedding or
reindex, and authenticated smoke gates must pass in order.
