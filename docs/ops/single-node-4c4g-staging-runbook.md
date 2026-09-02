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

## Restricted-network image delivery

The normal path is a direct pull of the exact immutable GHCR digests. The 4C4G Worker
has a bounded 30-minute rollout window to cover the measured cold-pull case. If that
window expires, stop the release and inspect the sanitized workflow evidence; do not
retry indefinitely against an unhealthy registry.

Use the reviewed `Relay Staging Images` workflow when GHCR remains unreachable or a
repeatable cold pull would exceed the bounded window. It produces one receipt per API,
Worker, consumer and Web OCI archive. Download the archive and the versioned
`scripts/import_staging_oci_archive.py` receiver out of band, then run the receiver
without `--confirm` first. This validates the archive checksum, every OCI descriptor,
the canonical import base and the receipt-bound deployment digest without changing
containerd. Review the output, then repeat the same command with `--confirm` and a
root-owned record path. Keep only the non-secret receipt, checksum and import result in
the release evidence. Do not copy signed URLs, registry credentials or Kubernetes
Secret contents into the repository or logs.

The canonical command sequence and descriptor checks are maintained in the
[4C8G restricted-network relay procedure](single-node-4c8g-staging-runbook.md#restricted-network-image-relay-import).
After all four images import successfully, dispatch a new staging run with the same
immutable digests and require every existing migration, rollout, embedding, readiness
and authenticated smoke gate to pass.

## Governance smoke

The main upload/Agent smoke proves the primary business path. A separate governance
smoke is available for a dedicated synthetic staging tenant when the protected
environment variable below is set:

```text
STAGING_RUN_GOVERNANCE_SMOKE=true
```

The operator must provision two short-lived environment secrets for that tenant:
`STAGING_GOVERNANCE_OWNER_TOKEN` and `STAGING_GOVERNANCE_MEMBER_TOKEN`. Do not paste
either token into a workflow input, command line, repository file or chat message.
The smoke verifies restricted-document grant/revoke, member denial, owner-only
retention/legal-hold governance, optional archive verification, and external identity
binding deactivate/reactivate. It writes only sanitized step status and bounded counts;
document IDs, actor IDs, emails, issuer/subject values, object keys and signed URLs are
not evidence.

The governance result is recorded separately from the ordinary rollout outcomes. A
required governance smoke that is skipped or fails keeps the staging release from
being promoted. This evidence still does not prove external IdP/SSO, complete ABAC,
WORM compliance, production capacity, HA or disaster recovery.
