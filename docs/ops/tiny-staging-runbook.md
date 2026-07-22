# Tiny Staging Runbook

This runbook describes the first real staging shape for the enterprise document
Agent. It is a single-node drill environment, not a production topology.

## Boundary

```text
GitHub Actions (manual environment)
        │ outbound runner connection
        v
2 vCPU / 2 GiB node
  |- K3s server + Traefik
  |- enterprise-doc-api / worker / consumer / web
  |- bounded ephemeral Redis delivery layer
  |- repository-scoped Actions runner (label: enterprise-doc-staging)
  `- cloudflared systemd service
        | outbound tunnel only
        v
Cloudflare hostname -> Traefik Ingress

External: PostgreSQL + pgvector, S3/R2 object storage, model provider,
retained observability and backups.
```

The node must not expose TCP `6443` publicly. The runner connects outbound to
GitHub, and `cloudflared` connects outbound to Cloudflare. The only public
application path is the Cloudflare hostname routed to Traefik.

## One-time host preparation

The numbered steps below are also the rebuild procedure. As of the 2026-07-22
observation, K3s and the repository-scoped runner are already installed and online;
the firewall and external-service gates are still incomplete. Re-run a completed step
only after a node replacement or an explicit rebuild decision.

1. Finish the pre-reset audit and copy the backup archive off the host. Do not
   run the reset while `fantasy-pet-app-api.service`,
   `fantasy-pet-worker-daemon.service`, `gamer` admin review, or Prometheus are
   still needed.
2. Reinstall a supported minimal Ubuntu image, set a unique hostname, enable
   SSH with a separate administrative key, and apply the provider security
   group. Allow SSH only from the operator network; allow `80/443` only if a
   direct ingress is retained. Keep `6443`, `8472`, and `10250` private.
3. Install K3s on the empty node. The repository's tiny profile expects the
   packaged Traefik ingress controller:

   ```bash
   curl -sfL https://get.k3s.io | K3S_KUBECONFIG_MODE=600 sh -
   sudo kubectl get nodes
   sudo kubectl get pods -A
   ```

   A 2C/2G node is only the K3s baseline; the profile keeps application
   memory limits bounded at 928 MiB and uses one replica per process.
4. Create a dedicated non-root account for the Actions runner. Register it at
   the private repository's **Settings → Actions → Runners** page with the
   custom label `enterprise-doc-staging`; install it as a systemd service and
   verify that it is online. Never register a public-repository runner and
   never run the runner as `root`.
5. Install `cloudflared` as a systemd service using a named tunnel and a
   configuration that routes `agent.playlab.eu.cc` to the Traefik listener.
   The current node has `cloudflared 2026.7.2` installed, but the service is
   intentionally not configured until a Dashboard-created Tunnel token exists.
   Keep the tunnel token/credentials in a root-readable file outside Git. Do
   not expose the Kubernetes API through the tunnel.
6. Before registering the runner, verify the host security boundary from an
   out-of-band console. The current host audit found UFW inactive and public
   listeners on `6443/tcp`, `10250/tcp`, and `8472/udp`. Restrict those ports
   in the provider security group and host firewall, while preserving SSH from
   the operator allowlist. Do not test this by editing firewall rules over the
   only SSH session.

The current runner was registered before that firewall gate was closed. Treat its
online state as an observed bootstrap fact, not an approved rollout condition. Do not
dispatch deploy or rollback while the control-plane ports remain public; if the
firewall cannot be corrected immediately, stop the runner with
`sudo /opt/actions-runner/svc.sh stop` and restart it only after an out-of-band
firewall verification.

## External prerequisites

Create these before the first workflow dispatch:

- the dedicated R2/S3 staging buckets (`documents` and `artifacts`) and an R2
  API token with the minimum bucket permissions;
- an external PostgreSQL/pgvector database and every currently resolved public
  egress `/32` (or `/128`) in the comma-separated `STAGING_DATABASE_EGRESS_CIDRS`;
- a certificate covering the staging hostname, or a Cloudflare-managed origin
  TLS arrangement that produces the Kubernetes Secret
  `enterprise-doc-staging-tls`;
- a GHCR pull Secret named `enterprise-doc-registry` using a narrowly scoped
  token with `read:packages` only;
- the application Secret `enterprise-doc-secrets` with the keys listed in
  `infra/k8s/base/secret.example.yaml`;
- a real model gateway exposing an OpenAI-compatible `/v1` endpoint over
  public HTTPS, an exact model identifier available from that endpoint, and a
  scoped API key. Store only `MODEL__API_KEY` in `enterprise-doc-secrets`;
  keep the non-secret endpoint and model identifier in GitHub Environment
  variables.

## Cloudflare account boundary

The `playlab.eu.cc` zone is owned by Cloudflare account
`2741446a7478f2d8a5ff31df7e077f17`. An inventory on 2026-07-22 found no
`agent.playlab.eu.cc` or `objects.agent.playlab.eu.cc` DNS records and no
Cloudflare Tunnels. The existing `playlab-web` bucket was left untouched; two
dedicated buckets, `documents` and `artifacts`, were created in the zone-owning
account and given a browser CORS policy for `https://agent.playlab.eu.cc`.
The connector's default account is different, so every DNS, Tunnel and R2 API
call for this staging environment must explicitly target the zone-owning
account. Do not reuse or mutate `playlab-web`.

The current Python S3 adapter uses two logical buckets, `documents` and
`artifacts`, and signs path-style S3 URLs. Provision both bucket names in the
same R2 account unless the adapter is deliberately changed to use prefixes in a
single bucket. `OBJECT_STORE__ENDPOINT` must remain the S3 API endpoint used by
Pods; for this account it is
`https://2741446a7478f2d8a5ff31df7e077f17.r2.cloudflarestorage.com`.
`OBJECT_STORE__PRESIGN_ENDPOINT` must be an endpoint that accepts those S3
SigV4 browser PUT requests. For R2, use the account S3 endpoint for both
control and presign clients unless a separate, tested upload proxy is adopted:
`https://2741446a7478f2d8a5ff31df7e077f17.r2.cloudflarestorage.com`. A public R2
custom domain is a public object-access surface, not a substitute for the S3
presign endpoint; do not attach one to the private documents bucket merely to
make the hostname resolve. Verify a real multipart PUT and follow-up HEAD with
scoped credentials before dispatching staging. Workload access remains blocked
until a separately scoped R2 API token is created for these buckets.

## GitHub configuration

The private `staging` Environment now exists. Its deployment profile, public
application host, R2 S3 host, Web origin, kube context, private API server and
namespace identity variables are configured. The scoped kubeconfig is also
stored. Add the remaining values after the database and smoke principal exist:

```text
Configured secret: STAGING_KUBECONFIG
Missing secret:    STAGING_SMOKE_TOKEN
Configured vars:   STAGING_KUBE_CONTEXT=enterprise-doc-staging
                   STAGING_KUBE_API_SERVER=https://127.0.0.1:6443
                   STAGING_NAMESPACE_UID=<current namespace UID>
Missing var:       STAGING_DATABASE_EGRESS_CIDRS=<reviewed-public-db-/32-list>
                   STAGING_MODEL_BASE_URL=https://<gateway-host>/v1
                   STAGING_MODEL_NAME=<exact-model-id>
                   STAGING_CONTROL_PLANE_APPROVED=true
Optional var:      STAGING_ROLLBACK_API_IMAGE=<previous-api-image@sha256:...>
                   STAGING_ROLLBACK_WORKER_IMAGE=<previous-worker-image@sha256:...>
                   STAGING_ROLLBACK_CONSUMER_IMAGE=<previous-consumer-image@sha256:...>
                   STAGING_ROLLBACK_WEB_IMAGE=<previous-web-image@sha256:...>
```

Configured variables are:

```text
STAGING_DEPLOYMENT_PROFILE=tiny-single-node
STAGING_ALLOWED_HOST=agent.playlab.eu.cc
STAGING_OBJECT_STORE_ALLOWED_HOST=2741446a7478f2d8a5ff31df7e077f17.r2.cloudflarestorage.com
VITE_OBJECT_STORE_ORIGINS=https://2741446a7478f2d8a5ff31df7e077f17.r2.cloudflarestorage.com
```

The deploy workflow fixes `MODEL__PROVIDER=openai_compatible`; the deterministic
test provider is intentionally forbidden in staging. It reads
`STAGING_MODEL_BASE_URL` and `STAGING_MODEL_NAME` from the protected `staging`
Environment rather than adding more manual inputs, because the workflow
already uses GitHub's ten-input `workflow_dispatch` limit. Configure them only
after the gateway contract is known:

```bash
gh variable set STAGING_MODEL_BASE_URL --env staging \
  --repo Drew-Z/enterprise-doc-agent --body 'https://<gateway-host>/v1'
gh variable set STAGING_MODEL_NAME --env staging \
  --repo Drew-Z/enterprise-doc-agent --body '<exact-model-id>'
```

Keep `STAGING_CONTROL_PLANE_APPROVED` unset until an out-of-band probe confirms the
provider security group and host firewall block public access to `6443/tcp`,
`10250/tcp` and `8472/udp`. The first workflow step requires the protected Environment
variable to equal `true` before checkout or kubeconfig access. Remove or set it to
`false` whenever the network boundary changes; it is an operator approval marker, not
a substitute for recurring network observation.

The manifest configurator rejects credentials, query strings, fragments,
loopback hosts, control characters and endpoints that do not end in `/v1`.
Changing either model variable changes the ConfigMap digest annotation and
therefore replaces API, Worker, consumer and migration Pods. The release
record includes only the non-secret provider, URL and model name; it never
includes `MODEL__API_KEY`.

The deploy and rollback workflows deliberately use the fixed runner label
`enterprise-doc-staging`; do not change it to `ubuntu-latest` or a generic
`self-hosted` label. Each job writes the kubeconfig to a unique file under
`runner.temp`, installs signal/error cleanup while creating it, and also removes it in
an `always()` step. An abrupt host or runner failure can still bypass process cleanup;
the runner's temporary-directory cleanup is a second layer, and the ServiceAccount
token must be rotated after any abnormal termination where residue cannot be ruled out.

The isolated runner uses a pre-provisioned toolchain instead of downloading
Python, kubectl and Kustomize during every release. Provision Python 3.12 at
`/opt/enterprise-doc-toolchain/python/bin/python`, install exactly `PyYAML==6.0.3`
and `cryptography==49.0.0` into that virtual environment, install Kustomize
`v5.7.1` on `PATH`, and provide a working kubectl client. Both workflows fail
before kubeconfig creation if this contract drifts. Keep the toolchain root-owned
and non-writable by the runner account; upgrades require a reviewed repository
change and an administrator-side toolchain update.

The node's direct Git HTTPS route can be unreliable even while GitHub's API and
source archive service remain reachable. Deploy and rollback therefore download
the exact `GITHUB_SHA` through the authenticated GitHub REST tarball endpoint,
with HTTPS-only redirects, bounded connection/runtime limits and retries. The
short-lived job token is written only to a mode-`600` curl configuration under
`runner.temp` and deleted by a trap. Extraction replaces only the guarded
`$GITHUB_WORKSPACE/repository` subdirectory; shell steps run from that directory.

## Runner registration (repository-scoped)

Run these commands as a dedicated non-root account on the node after the
firewall and outbound connectivity checks pass. Obtain the short-lived token
from the repository's **Settings -> Actions -> Runners -> New self-hosted
runner** page; never commit or paste it into a workflow file.

```bash
sudo useradd --create-home --shell /bin/bash gha-staging
sudo install -d -o gha-staging -g gha-staging /opt/actions-runner
sudo -iu gha-staging bash
cd /opt/actions-runner
curl -o actions-runner.tar.gz -L https://github.com/actions/runner/releases/download/v2.336.0/actions-runner-linux-x64-2.336.0.tar.gz
echo '04cf0be1aff4c3ec3554466c39124ca250e3effd8873bb7e8d68535aa9505d5d  actions-runner.tar.gz' | sha256sum -c -
tar xzf actions-runner.tar.gz
./config.sh --url https://github.com/Drew-Z/enterprise-doc-agent \
  --token '<one-time-registration-token>' \
  --name '<unique-runner-name>' \
  --labels enterprise-doc-staging --unattended
exit
sudo /opt/actions-runner/svc.sh install gha-staging
sudo /opt/actions-runner/svc.sh start
```

Confirm the runner is online and has exactly the expected custom label before
adding any staging secrets. The current runner name is intentionally redacted from
repository evidence; it runs as `gha-staging` with Runner `2.336.0`. The runner must
not be shared with public-repo or untrusted jobs.

## Scoped Kubernetes credentials

The repository includes a non-secret administrator bootstrap bundle containing
`infra/k8s/bootstrap/staging-deployer-rbac.yaml` and
`infra/k8s/bootstrap/staging-deployer-guardrails.yaml`. Together they define the
scoped RBAC and four parameter-free ValidatingAdmissionPolicy/Binding pairs. Apply the
whole bundle with an administrative kubeconfig during node provisioning and whenever
the reviewed guardrails change:

```bash
kubectl apply -k infra/k8s/bootstrap
kubectl get validatingadmissionpolicy,validatingadmissionpolicybinding \
  | grep enterprise-doc-staging
```

It creates the staging namespace, a non-root deployer ServiceAccount and a short,
explicit RBAC surface. The deployment identity can read administrator-owned
prerequisites and diagnostics, create/update reviewed Deployments and Jobs, and get
only the staging Namespace. It cannot read any Kubernetes Secret, create arbitrary
Pods, change ConfigMaps/Services/Ingress/NetworkPolicy/PDB objects, or patch Namespace
approvals; in particular, it cannot read its own long-lived token Secret. Admission
further restricts Deployment and Job names, immutable images,
entrypoints, environment sources, identities, probes and volumes. The identity has no
`cluster-admin` binding.

Confirm the negative and positive permissions before exporting credentials:

```bash
deployer='system:serviceaccount:enterprise-doc-agent-staging:enterprise-doc-staging-deployer'
test "$(kubectl auth can-i get secrets -n enterprise-doc-agent-staging --as "$deployer")" = no
test "$(kubectl auth can-i create pods -n enterprise-doc-agent-staging --as "$deployer")" = no
test "$(kubectl auth can-i update configmaps -n enterprise-doc-agent-staging --as "$deployer")" = no
test "$(kubectl auth can-i patch networkpolicies.networking.k8s.io \
  -n enterprise-doc-agent-staging --as "$deployer")" = no
test "$(kubectl auth can-i create jobs.batch -n enterprise-doc-agent-staging --as "$deployer")" = yes
test "$(kubectl auth can-i patch deployments.apps -n enterprise-doc-agent-staging \
  --as "$deployer")" = yes
```

Generate the kubeconfig on the node and pass it to the GitHub Environment without
committing it:

```bash
set -eu
umask 077
namespace=enterprise-doc-agent-staging
token="$(kubectl -n "$namespace" get secret enterprise-doc-staging-deployer-token \
  -o jsonpath='{.data.token}' | base64 -d | tr -d '\r\n')"
ca="$(base64 -w0 /var/lib/rancher/k3s/server/tls/server-ca.crt)"
namespace_uid="$(kubectl get namespace "$namespace" -o jsonpath='{.metadata.uid}')"
tmp="$(mktemp)"
trap 'rm -f "$tmp"; unset token ca namespace_uid' EXIT
cat >"$tmp" <<EOF
apiVersion: v1
kind: Config
clusters:
- name: enterprise-doc-staging
  cluster:
    server: https://127.0.0.1:6443
    certificate-authority-data: ${ca}
contexts:
- name: enterprise-doc-staging
  context:
    cluster: enterprise-doc-staging
    namespace: enterprise-doc-agent-staging
    user: enterprise-doc-staging-deployer
current-context: enterprise-doc-staging
users:
- name: enterprise-doc-staging-deployer
  user:
    token: ${token}
EOF
gh secret set STAGING_KUBECONFIG --env staging --repo Drew-Z/enterprise-doc-agent <"$tmp"
gh variable set STAGING_KUBE_CONTEXT --env staging --repo Drew-Z/enterprise-doc-agent \
  --body enterprise-doc-staging
gh variable set STAGING_KUBE_API_SERVER --env staging --repo Drew-Z/enterprise-doc-agent \
  --body https://127.0.0.1:6443
gh variable set STAGING_NAMESPACE_UID --env staging --repo Drew-Z/enterprise-doc-agent \
  --body "$namespace_uid"
```

The loopback API address is intentional: the deploy and rollback jobs run on
the same private node. Rotate the ServiceAccount token and the GitHub Secret
after operator access changes.

## Administrator prerequisite approval

The deploy workflow never applies Namespace, ConfigMap, ServiceAccount, Service,
Ingress, NetworkPolicy or PDB objects. Before the first rollout, and whenever an
endpoint, model route, image allowlist or prerequisite manifest changes, an
administrator must generate the exact same render in a disposable checkout of the
reviewed commit, inspect it, and apply only the prerequisite phase. Use the same
values that will be supplied to the protected `staging` Environment and workflow:

```bash
set -euo pipefail

# Set these without placing credentials in shell history or the repository.
PROFILE=tiny-single-node
REGISTRY_PREFIX=ghcr.io/drew-z
API_DIGEST=sha256:<64-hex>
WORKER_DIGEST=sha256:<64-hex>
CONSUMER_DIGEST=sha256:<64-hex>
WEB_DIGEST=sha256:<64-hex>
STAGING_BASE_URL=https://agent.playlab.eu.cc
OBJECT_STORE_ENDPOINT=https://<account>.r2.cloudflarestorage.com
OBJECT_STORE_PRESIGN_ENDPOINT="$OBJECT_STORE_ENDPOINT"
TLS_SECRET_NAME=enterprise-doc-staging-tls
WEB_OBJECT_STORE_ORIGINS="$OBJECT_STORE_PRESIGN_ENDPOINT"
DATABASE_EGRESS_CIDR=<comma-separated-reviewed-public-db-/32-list>
MODEL_BASE_URL=https://<gateway-host>/v1
MODEL_NAME=<exact-model-id>
ROLLBACK_API_IMAGE=${ROLLBACK_API_IMAGE:-}
ROLLBACK_WORKER_IMAGE=${ROLLBACK_WORKER_IMAGE:-}
ROLLBACK_CONSUMER_IMAGE=${ROLLBACK_CONSUMER_IMAGE:-}
ROLLBACK_WEB_IMAGE=${ROLLBACK_WEB_IMAGE:-}

pushd infra/k8s/overlays/staging
kustomize edit set image enterprise-doc/api="$REGISTRY_PREFIX/enterprise-doc-api@$API_DIGEST"
kustomize edit set image enterprise-doc/worker="$REGISTRY_PREFIX/enterprise-doc-worker@$WORKER_DIGEST"
kustomize edit set image enterprise-doc/consumer="$REGISTRY_PREFIX/enterprise-doc-consumer@$CONSUMER_DIGEST"
kustomize edit set image enterprise-doc/web="$REGISTRY_PREFIX/enterprise-doc-web@$WEB_DIGEST"
popd

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
kustomize build "infra/k8s/overlays/$PROFILE" > "$workdir/staging-template.yaml"
python scripts/configure_staging_manifest.py \
  --input "$workdir/staging-template.yaml" \
  --output "$workdir/staging.yaml" \
  --staging-base-url "$STAGING_BASE_URL" \
  --object-store-endpoint "$OBJECT_STORE_ENDPOINT" \
  --object-store-presign-endpoint "$OBJECT_STORE_PRESIGN_ENDPOINT" \
  --tls-secret-name "$TLS_SECRET_NAME" \
  --web-object-store-origins "$WEB_OBJECT_STORE_ORIGINS" \
  --database-egress-cidr "$DATABASE_EGRESS_CIDR" \
  --model-provider openai_compatible \
  --model-base-url "$MODEL_BASE_URL" \
  --model-name "$MODEL_NAME" \
  --rollback-api-image "$ROLLBACK_API_IMAGE" \
  --rollback-worker-image "$ROLLBACK_WORKER_IMAGE" \
  --rollback-consumer-image "$ROLLBACK_CONSUMER_IMAGE" \
  --rollback-web-image "$ROLLBACK_WEB_IMAGE"
python scripts/render_k8s_phase.py --input "$workdir/staging.yaml" \
  --phase prerequisites --output "$workdir/staging-prerequisites.yaml"

kubectl apply --dry-run=server -f "$workdir/staging-prerequisites.yaml"
kubectl apply -f "$workdir/staging-prerequisites.yaml"
kubectl get namespace enterprise-doc-agent-staging -o yaml \
  > "$workdir/live-prerequisites.yaml"
kubectl -n enterprise-doc-agent-staging get \
  configmaps,serviceaccounts,services,poddisruptionbudgets.policy,ingresses.networking.k8s.io,networkpolicies.networking.k8s.io \
  -o yaml >> "$workdir/live-prerequisites.yaml"
kubectl get namespace enterprise-doc-agent-staging -o json > "$workdir/live-namespace.json"
python scripts/validate_staging_prerequisites.py \
  --expected-manifest "$workdir/staging-prerequisites.yaml" \
  --live-manifest "$workdir/live-prerequisites.yaml" \
  --live-namespace-json "$workdir/live-namespace.json"
```

The Namespace approval contains only reviewed non-secret routes, immutable image
identities and SHA-256 fingerprints. Never place API keys, database DSNs, bearer
tokens, private keys or kubeconfig data in annotations.

Validate actual Secret contents separately with the administrative identity. The raw
Secret list must stay in the temporary directory; only the redacted report may be
retained as external evidence:

```bash
kubectl -n enterprise-doc-agent-staging get secrets \
  enterprise-doc-secrets enterprise-doc-registry "$TLS_SECRET_NAME" -o json \
  > "$workdir/staging-secrets.json"
python scripts/validate_staging_secrets.py \
  --input "$workdir/staging-secrets.json" \
  --staging-host agent.playlab.eu.cc \
  --tls-secret-name "$TLS_SECRET_NAME" \
  --output "$workdir/staging-secrets-redacted.json"
rm -f "$workdir/staging-secrets.json"
```

After a guardrail change, exercise the allow/deny matrix against the real API server.
The verifier uses unique temporary policy names and restores Namespace annotations in
`finally`; it does not create workloads because every apply is server-side dry-run:

```bash
python scripts/verify_staging_admission.py \
  --bootstrap-dir infra/k8s/bootstrap \
  --rendered-manifest "$workdir/staging.yaml" \
  --smoke-job infra/k8s/smoke/readiness-job.yaml
test -z "$(kubectl get validatingadmissionpolicy -o name | grep -- '-verify-' || true)"
test -z "$(kubectl get validatingadmissionpolicybinding -o name | grep -- '-verify-' || true)"
```

## First rollout

Do not dispatch with the `v0.1.1` Web digest: its Vite bundle was built with
`https://objects.agent.playlab.eu.cc`, and that public custom domain is not the
reviewed R2 S3 presign endpoint. Publish a new tagged release after the
repository `VITE_OBJECT_STORE_ORIGINS` variable targets the account R2 S3
endpoint, then use all four immutable digests from the new strict release
manifest. The workflow renders the selected profile, verifies every administrator-owned
prerequisite and Namespace approval, binds the context to the expected API server and
Namespace UID, validates workload updates, replaces the completed fixed-name migration
Job with a server-side create dry-run, waits for completion, applies workloads,
performs in-cluster readiness and authenticated upload → ingestion → Agent
smoke, and uploads sanitized evidence.

The first digest-pinned release may need to cold-pull images over a constrained
route. Kubernetes counts that download time against a Job's active deadline, so
the migration Job has a 900-second total budget and application rollouts have a
600-second budget each; the enclosing deploy job is capped at 45 minutes. A
timeout while the migration Pod is still `ContainerCreating` means Alembic never
started. Preserve the failed Job and events for review, confirm that state, then
delete it explicitly before retrying; never treat an image-pull timeout as a
completed migration.

Before dispatch, confirm `STAGING_MODEL_BASE_URL`, `STAGING_MODEL_NAME` and the
`MODEL__API_KEY` Secret value all refer to the same gateway account and model.
The API, Worker, consumer and migration processes all load the staging model
settings, so an incomplete model contract blocks startup before smoke testing.

Do not mark the gate passed if a step is skipped, the smoke is disabled, or the
workflow ran on a different runner. Record the run URL, commit, profile,
digests, migration revision, smoke result, and evidence artifact hashes.

## Rollback and recovery

Rollback uses the **Rollback Release** workflow with explicit deployment
revisions and a reviewed migration revision. A rollout undo does not reverse a
destructive database migration. For data recovery, restore to a separately
named staging database first, verify the backup SHA-256, run application smoke,
then document measured restore and rollback times as RPO/RTO evidence.

Kubernetes provides no transaction across Deployments. The workflow dry-runs every
requested revision before submitting any undo, which prevents predictable admission or
revision errors from causing an avoidable split, but a runtime/API failure during the
actual mutations can still leave mixed revisions. Use the uploaded structured command
record to identify completed and failed mutations, stop further promotion, and reconcile
all Deployments explicitly; do not describe this as an atomic application rollback.

## Resource guardrails

```bash
kubectl top node
kubectl -n enterprise-doc-agent-staging top pods
kubectl -n enterprise-doc-agent-staging get events --sort-by=.lastTimestamp
free -h
```

If memory pressure or swap activity appears during migration, stop the rollout
and move staging to a larger node. Do not compensate by removing probes,
security contexts, or digest pinning.
