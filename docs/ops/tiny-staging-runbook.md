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
   memory limits bounded at 992 MiB and uses one replica per process.
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
  variables;
- a real OpenAI-compatible embedding endpoint and reviewed 1024-dimensional model.
  Store only `EMBEDDING__API_KEY` in `enterprise-doc-secrets`; follow
  `docs/ops/real-embedding-rollout.md` without modifying the blog vector store.

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

R2 staging must use `OBJECT_STORE__MULTIPART_CHECKSUM_MODE=readback_sha256`.
Cloudflare R2 currently rejects the SHA-256 transport checksum fields on
`UploadPart`/`CompleteMultipartUpload` (HTTP 501). In readback mode the client
still submits the expected SHA-256 values to the API, while the service completes
the ordinary S3 multipart request and reads each completed range back before it
creates a `DocumentVersion`. This adds one bounded read per part and deliberately
does not claim a provider transport checksum. Native S3/MinIO deployments may
continue using `native_sha256`. Never treat an incomplete R2 multipart ETag as a
verified SHA-256 value.

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
                   STAGING_OBJECT_STORE_CHECKSUM_MODE=readback_sha256
                   STAGING_MODEL_BASE_URL=https://<gateway-host>/v1
                   STAGING_MODEL_NAME=<exact-model-id>
                   STAGING_EMBEDDING_BASE_URL=https://<embedding-host>/v1
                   STAGING_EMBEDDING_MODEL_NAME=Qwen/Qwen3-Embedding-4B
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
`STAGING_MODEL_BASE_URL`, `STAGING_MODEL_NAME`, `STAGING_EMBEDDING_BASE_URL`,
`STAGING_EMBEDDING_MODEL_NAME` and
`STAGING_OBJECT_STORE_CHECKSUM_MODE` from the protected `staging` Environment rather
than exceeding GitHub's ten-input `workflow_dispatch` limit. Configure them only
after the gateway contract is known:

```bash
gh variable set STAGING_MODEL_BASE_URL --env staging \
  --repo Drew-Z/enterprise-doc-agent --body 'https://<gateway-host>/v1'
gh variable set STAGING_MODEL_NAME --env staging \
  --repo Drew-Z/enterprise-doc-agent --body '<exact-model-id>'
gh variable set STAGING_EMBEDDING_BASE_URL --env staging \
  --repo Drew-Z/enterprise-doc-agent --body 'https://<embedding-host>/v1'
gh variable set STAGING_EMBEDDING_MODEL_NAME --env staging \
  --repo Drew-Z/enterprise-doc-agent --body 'Qwen/Qwen3-Embedding-4B'
gh variable set STAGING_OBJECT_STORE_CHECKSUM_MODE --env staging \
  --repo Drew-Z/enterprise-doc-agent --body 'readback_sha256'
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
and `cryptography==50.0.0` into that virtual environment, install Kustomize
`v5.7.1` on `PATH`, install the reviewed
`postgresql-client-17=17.10-1.pgdg24.04+1`, and provide a working kubectl client. The
host toolchain check verifies the exact PostgreSQL package plus the
`pg_dump`/`pg_restore` major. Both workflows fail
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

The bootstrap Namespace contains only the stable profile and Pod Security baseline; the
generated administrator prerequisites add release-specific approval annotations. A later
client-side bootstrap apply therefore restores that baseline and removes those approvals.
After every bootstrap update, immediately reapply the generated administrator
prerequisites and rerun `validate_staging_prerequisites.py` before dispatching deploy or
rollback. Admission fails closed while the approval annotations are absent; do not leave
the cluster in that intermediate state.

It creates the staging namespace, a non-root deployer ServiceAccount and a short,
explicit RBAC surface. The deployment identity can read administrator-owned
prerequisites, diagnostics and ReplicaSet rollout history, create/update reviewed
Deployments and Jobs, and get only the staging Namespace. ReplicaSets remain read-only.
It cannot read any Kubernetes Secret, create arbitrary Pods, change
ConfigMaps/PVCs/Services/Ingress/NetworkPolicy/PDB objects, or patch Namespace approvals;
in particular, it cannot read its own long-lived token Secret. Admission further
restricts Deployment and Job names, immutable images,
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
test "$(kubectl auth can-i patch persistentvolumeclaims \
  -n enterprise-doc-agent-staging --as "$deployer")" = no
test "$(kubectl auth can-i create jobs.batch -n enterprise-doc-agent-staging --as "$deployer")" = yes
test "$(kubectl auth can-i patch deployments.apps -n enterprise-doc-agent-staging \
  --as "$deployer")" = yes
test "$(kubectl auth can-i list replicasets.apps -n enterprise-doc-agent-staging \
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

The deploy workflow never applies Namespace, ConfigMap, PVC, ServiceAccount, Service,
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
OBJECT_STORE_CHECKSUM_MODE=readback_sha256
TLS_SECRET_NAME=enterprise-doc-staging-tls
WEB_OBJECT_STORE_ORIGINS="$OBJECT_STORE_PRESIGN_ENDPOINT"
DATABASE_EGRESS_CIDR=<comma-separated-reviewed-public-db-/32-list>
MODEL_BASE_URL=https://<gateway-host>/v1
MODEL_NAME=<exact-model-id>
ROLLBACK_API_IMAGE=${ROLLBACK_API_IMAGE:-}
ROLLBACK_WORKER_IMAGE=${ROLLBACK_WORKER_IMAGE:-}
ROLLBACK_CONSUMER_IMAGE=${ROLLBACK_CONSUMER_IMAGE:-}
ROLLBACK_WEB_IMAGE=${ROLLBACK_WEB_IMAGE:-}

# tiny-single-node inherits staging; bind logical image names in the parent
# before rendering the selected profile.
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
  --object-store-checksum-mode "$OBJECT_STORE_CHECKSUM_MODE" \
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
printf '\n---\n' >> "$workdir/live-prerequisites.yaml"
kubectl -n enterprise-doc-agent-staging get \
  configmaps,persistentvolumeclaims,serviceaccounts,services,poddisruptionbudgets.policy,ingresses.networking.k8s.io,networkpolicies.networking.k8s.io \
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
if kubectl -n enterprise-doc-agent-staging get job enterprise-doc-migrate >/dev/null 2>&1; then
  previous_complete="$(kubectl -n enterprise-doc-agent-staging get job enterprise-doc-migrate \
    -o jsonpath='{.status.conditions[?(@.type=="Complete")].status}')"
  test "$previous_complete" = True
  backup_dir="$(mktemp -d)"
  kubectl -n enterprise-doc-agent-staging get job enterprise-doc-migrate -o yaml \
    > "$backup_dir/enterprise-doc-migrate.yaml"
  kubectl -n enterprise-doc-agent-staging logs job/enterprise-doc-migrate \
    > "$backup_dir/enterprise-doc-migrate.log" 2>&1 || true
  kubectl -n enterprise-doc-agent-staging delete job enterprise-doc-migrate --wait=true
fi
python scripts/verify_staging_admission.py \
  --bootstrap-dir infra/k8s/bootstrap \
  --rendered-manifest "$workdir/staging.yaml" \
  --smoke-job infra/k8s/smoke/readiness-job.yaml
test -z "$(kubectl get validatingadmissionpolicy -o name | grep -- '-verify-' || true)"
test -z "$(kubectl get validatingadmissionpolicybinding -o name | grep -- '-verify-' || true)"
```

The completed fixed-name migration Job must be absent when its Pod template changes.
Kubernetes immutable-field validation runs before the admission dry-run can prove the
new command allowlist; retain its YAML and logs before deletion.

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
runs and waits for the internal Prometheus Deployment when the `single-node-4c8g`
profile is selected,
runs the restricted `enterprise-doc-embedding-rollout` Job to probe the provider and
converge ready documents to the approved generation, performs in-cluster readiness and
authenticated upload → ingestion → Agent smoke, and uploads sanitized evidence.

The reviewed migration command runs `alembic upgrade head`,
`enterprise-doc-checkpointer-setup --setup`, and then `--check`. Tiny staging uses
15-second database/object-store connection budgets, a 60-second checkpointer
budget, and a 70-second Worker readiness probe timeout based on measured external
dependency latency. These values are staging-specific and do not redefine the
production defaults.

The embedding rollout CLI has a 1,200-second convergence deadline, its Job has a
1,260-second active deadline, and the workflow waits 1,320 seconds. It emits one
redacted JSON report and succeeds only when the probe contract passes and the final
reindex plan reports `selected=0`. Admission fixes the Job name, current approved API
digest, command, arguments, environment sources, runtime identity and security context;
the deployer still has no `pods/exec` or Secret read permission. Preserve an incomplete
or failed Job for operator review, then delete it explicitly before retrying.

The first digest-pinned release may need to cold-pull images over a constrained
route. Kubernetes counts that download time against a Job's active deadline, so
the migration Job has a 2,700-second total budget, the workflow waits up to
2,760 seconds for its terminal condition, and application rollouts have a
600-second budget each; the enclosing deploy job is capped at 90 minutes. A
timeout while the migration Pod is still `ContainerCreating` means Alembic never
started. Preserve the failed Job and events for review, confirm that state, then
delete it explicitly before retrying; never treat an image-pull timeout as a
completed migration.

The `v0.1.4` attempt on 2026-07-22 measured the original bound rather than a
migration defect: one 78,160,987-byte API layer had downloaded only about 36 MiB
when the 900-second Job deadline terminated the still-creating Pod. The failed
run retained sanitized evidence and did not roll out any new workloads. That
measurement is the basis for the bounded cold-pull budget above.

Before dispatch, confirm `STAGING_MODEL_BASE_URL`, `STAGING_MODEL_NAME` and the
`MODEL__API_KEY` Secret value all refer to the same gateway account and model. Also
confirm `STAGING_EMBEDDING_BASE_URL`, `STAGING_EMBEDDING_MODEL_NAME` and
`EMBEDDING__API_KEY` identify the reviewed embedding route.
The API, Worker, consumer, migration and embedding rollout processes all load the
staging model settings, so an incomplete model contract blocks startup before smoke
testing.

The authenticated smoke client identifies itself as
`enterprise-doc-staging-smoke/1.0`. The staging hostname must allow this API
automation User-Agent through the Cloudflare security layer; a Browser Integrity
Check block returns Cloudflare error 1010 before the request reaches the API and
must be diagnosed at the correct Cloudflare account, not treated as a JWT or
database authorization failure.

`STAGING_SMOKE_TOKEN` is intentionally short-lived. Rotate it immediately before a
deployment window with the administrative kubeconfig and the dedicated, already active
smoke membership. The script refuses non-staging environments, verifies the exact
tenant/user membership against PostgreSQL, and emits only the JWT. It uses the Pod's
existing authentication configuration in-process, but never prints or exports signing-key
or database credentials. Run this from the repository root with the reviewed
administrative kubeconfig. Do not enable shell xtrace or terminal recording:

```bash
set -euo pipefail
namespace=enterprise-doc-agent-staging
STAGING_SMOKE_TENANT_ID=<reviewed-smoke-tenant-uuid>
STAGING_SMOKE_ACTOR_ID=<reviewed-smoke-user-uuid>
kubectl -n "$namespace" rollout status \
  deployment/enterprise-doc-api --timeout=300s
pod="$(kubectl -n "$namespace" get pod \
  -l app.kubernetes.io/name=enterprise-doc-api \
  --field-selector=status.phase=Running \
  -o jsonpath='{.items[0].metadata.name}')"
test -n "$pod"
kubectl -n "$namespace" wait --for=condition=Ready \
  "pod/$pod" --timeout=60s
trap 'unset token' EXIT
token="$(kubectl -n "$namespace" exec -i "$pod" -c api -- \
  python - \
  --tenant-id "$STAGING_SMOKE_TENANT_ID" \
  --actor-id "$STAGING_SMOKE_ACTOR_ID" \
  < scripts/issue_staging_smoke_token.py)"
printf '%s' "$token" | gh secret set STAGING_SMOKE_TOKEN \
  --env staging --repo Drew-Z/enterprise-doc-agent
unset token
trap - EXIT
```

Do not select an arbitrary active membership or store the token in a file. A smoke HTTP
401 means the release gate failed; rotate the reviewed token and rerun the entire deploy
workflow so migration, rollout and both smoke layers are evidenced together.

Do not mark the gate passed if a step is skipped, the smoke is disabled, the embedding
report is absent or invalid, or the workflow ran on a different runner. Record the run
URL, commit, profile, digests, migration revision, embedding identity and convergence
summary, smoke result, and evidence artifact hashes.

The first retained-observability rollout for `single-node-4c8g` completed in
[Deploy Staging run 30970431550](https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/30970431550)
at commit `2b33f7c`. The operator-only drill verified the 5 GiB PVC, all three scrape
targets, all nine rules and retained samples across Prometheus Pod replacement. The
query scope, observed counts and limitations are recorded in
`docs/ops/staging-observability-and-capacity.md`.

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

Before dispatching rollback, map every requested Deployment revision to its immutable
image and compare all four images with the same passed release record. The same images
must also appear in the administrator-owned Namespace approval annotations. Do not build a
rollback target by combining API, Worker, Consumer or Web revisions from different runs,
even if each image was previously healthy by itself. The 2026-08-05 preflight found an
incoherent live allowlist; it was replaced with the four images from passed Deploy Staging
run `30939628894`, which maps all four Deployments to revision 8. Rollback Release run
`30976323048` then failed before mutation because the scoped deployer could not list
ReplicaSets. Run `30979001847` also failed before mutation when a bootstrap update removed
the release-specific Namespace approvals; the exact reviewed prerequisite manifest was
reapplied and its SHA-256 `052310d2916a014ed6063e4c626f7c734981a86c7419ea3aa5b2f76358d094eb`
passed all 17 approval-key and 25 live-object checks.

The bidirectional drill then passed. Run `30979462976` rolled all four Deployments from
revision 9 images to the coherent revision 8 images, which Kubernetes recorded as revision
10; authenticated upload, ingestion and Agent smoke passed in 69.219 seconds. Run
`30979754380` restored the original revision 9 images as revision 11; the same smoke passed
in 72.187 seconds. The sanitized command timings, exact images, safe preflight failures and
limitations are retained in
`evidence/m6/20260805-staging-bidirectional-rollback.json`. This closes the staging release
rollback sub-gate, but it does not close database/object recovery or production RPO/RTO.

### Isolated staging database restore drill

Never point `restore_database.py` at the live staging database. Provision a separately
named database beginning with `enterprise_doc_restore_`, keep every application workload
on the source database, and use a PostgreSQL client whose major version is at least the
server major version. Install and verify that pinned boundary with
`infra/host/ubuntu-24.04/provision-runner-toolchain.sh`; do not install an unpinned
`postgresql-client` package by hand.

Supabase application backups must be scoped to the reviewed application schema. A full
database dump includes provider-owned `auth`, `realtime`, `storage` and `vault` objects
that the application role must not recreate. Create the custom archive with the secure
backup helper and retain its digest:

```bash
install -d -m 0700 /secure/off-repo/path
python scripts/backup_database.py \
  --output /secure/off-repo/path/staging-public.dump \
  --schema public \
  --record-path /secure/off-repo/path/backup-record.json
```

Create the isolated target from `template0` and install only the extension required by
the repository migrations before restore:

```bash
restore_database=enterprise_doc_restore_$(date -u +%Y%m%dT%H%M%SZ | tr '[:upper:]' '[:lower:]')
createdb --maintenance-db=postgres --template=template0 "$restore_database"
psql --dbname "$restore_database" --set=ON_ERROR_STOP=1 \
  --command 'CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public'
```

These commands rely on reviewed libpq environment variables. Do not put a database URL
or password in an argument. Resolve the connected server address with the read-only dry
run and pin that observed address during confirmation.

Keep the target URL out of shell history and process arguments. From the repository root,
run the validation pass first and add `--confirm` only after reviewing the target host,
server address, source database, target database, backup digest and archive selection:

```bash
set -euo pipefail
source_database=postgres
restore_database=enterprise_doc_restore_$(date -u +%Y%m%dT%H%M%SZ)
expected_host=<reviewed-postgresql-host>
backup_path=/secure/off-repo/path/staging-public.dump
backup_sha256=<reviewed-64-hex-sha256>
expected_server_address=<reviewed-inet-server-address>
record_path=/secure/off-repo/path/staging-restore-record.json

restore_args=(
  --input "$backup_path"
  --database-url-env RESTORE_DATABASE_URL
  --environment staging
  --expected-host "$expected_host"
  --expected-server-address "$expected_server_address"
  --expected-database "$restore_database"
  --preexisting-schema public
  --source-database "$source_database"
  --expected-sha256 "$backup_sha256"
  --record-path "$record_path"
)

read -rsp 'Isolated restore DATABASE URL: ' RESTORE_DATABASE_URL
printf '\n'
export RESTORE_DATABASE_URL
python scripts/restore_database.py "${restore_args[@]}"

# After reviewing the dry run, execute the exact same boundary with confirmation.
python scripts/restore_database.py "${restore_args[@]}" --confirm
unset RESTORE_DATABASE_URL
```

After restore, compare migration revisions and bounded table counts, then run read-only
application validation against the isolated target before deleting it. Retain only the
sanitized restore record and aggregate validation results. The command does not restore or
validate R2 objects, so a database-only drill cannot close the cross-system recovery or
production RPO/RTO gate. The 2026-08-05 external drill passed with 25 exact table counts,
Alembic revision `20260804_0011`, and application checkpoint readiness; its sanitized
record is `evidence/m6/20260805-staging-postgres-restore.json`.

### R2 immutable snapshot and isolated-prefix restore drill

R2 does not implement S3 bucket versioning, so there is no `VersionId` recovery path.
Use the repository-owned application snapshot instead. It enumerates `DocumentVersion`
and durable `AgentArtifact` references from PostgreSQL, checks source size and streamed
SHA-256, copies every object into a drill-specific namespace, and uploads the manifest
only after readback succeeds. Objects larger than R2's 4.995 GiB single-request limit use
multipart copy. The manifest contains private object keys and reference IDs; retain it
off-repository with mode `0600` and never upload it as ordinary deployment evidence.

Before the confirmed snapshot, add a **Bucket lock rule** to both the documents and
artifacts buckets. Use the exact prefix `enterprise-doc-recovery/snapshots/<drill-id>/`
and a reviewed retention period. Bucket Lock is a Cloudflare control-plane feature; the
S3 access key, S3 Object Lock and bucket-versioning APIs are not substitutes. Use a
Cloudflare API token through the `CLOUDFLARE_API_TOKEN` environment variable, never argv,
and pin Wrangler for repeatability. The token requires account-level
`Workers R2 Storage Write`; bucket-item-only or S3 credentials cannot edit bucket
configuration. Record the rule names, prefixes and retention end without retaining the
token. See the official [Bucket locks documentation](https://developers.cloudflare.com/r2/buckets/bucket-locks/),
[Wrangler environment variables](https://developers.cloudflare.com/workers/wrangler/system-environment-variables/)
and [R2 limits](https://developers.cloudflare.com/r2/platform/limits/).

```bash
set -euo pipefail
wrangler_version=4.119.0
account_id=2741446a7478f2d8a5ff31df7e077f17
drill_id=20260806-staging-r2
snapshot_prefix="enterprise-doc-recovery/snapshots/${drill_id}/"
lock_rule_id="enterprise-doc-${drill_id}"
lock_retention_date=$(date -u -d '+14 days' +%F)
documents_bucket=documents
artifacts_bucket=artifacts
lock_record=/secure/off-repo/path/${drill_id}-bucket-lock.json

read -rsp 'Cloudflare control-plane API token: ' CLOUDFLARE_API_TOKEN
printf '\n'
export CLOUDFLARE_API_TOKEN
export CLOUDFLARE_ACCOUNT_ID="$account_id"

npx --yes "wrangler@${wrangler_version}" r2 bucket lock add \
  "$documents_bucket" "$lock_rule_id" "$snapshot_prefix" \
  --retention-date "$lock_retention_date" --force
npx --yes "wrangler@${wrangler_version}" r2 bucket lock add \
  "$artifacts_bucket" "$lock_rule_id" "$snapshot_prefix" \
  --retention-date "$lock_retention_date" --force

uv run python scripts/verify_r2_bucket_lock.py \
  --account-id "$account_id" \
  --bucket "$documents_bucket" \
  --bucket "$artifacts_bucket" \
  --prefix "$snapshot_prefix" \
  --rule-id "$lock_rule_id" \
  --minimum-retention-until "${lock_retention_date}T00:00:00Z" \
  --output "$lock_record"

unset CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID
```

Run the command from the deployed API image so it uses the same database schema, object
store client and environment-only credentials as staging. `/dev/shm` is used because the
container root filesystem is read-only. The CLI is dry-run by default and stdout contains
only aggregate counts and hashes:

```bash
set -euo pipefail
namespace=enterprise-doc-agent-staging
pod=$(kubectl -n "$namespace" get pod \
  -l app.kubernetes.io/name=enterprise-doc-api \
  -o jsonpath='{.items[0].metadata.name}')
drill_id=<reviewed-lowercase-drill-id>
endpoint_host=<reviewed-account-id>.r2.cloudflarestorage.com
documents_bucket=<reviewed-private-documents-bucket>
artifacts_bucket=<reviewed-private-artifacts-bucket>
remote_manifest=/dev/shm/${drill_id}-r2-snapshot.json
remote_record=/dev/shm/${drill_id}-r2-snapshot-record.json
local_manifest=/secure/off-repo/path/${drill_id}-r2-snapshot.json
local_record=/secure/off-repo/path/${drill_id}-r2-snapshot-record.json

snapshot_args=(
  --drill-id "$drill_id"
  --expected-endpoint-host "$endpoint_host"
  --allowed-bucket "$documents_bucket"
  --allowed-bucket "$artifacts_bucket"
  --manifest-bucket "$documents_bucket"
  --manifest-path "$remote_manifest"
  --record-path "$remote_record"
)

kubectl -n "$namespace" exec "$pod" -c api -- \
  enterprise-doc-object-snapshot "${snapshot_args[@]}"

# Review the dry-run counts and both Cloudflare Bucket Lock rules first.
kubectl -n "$namespace" exec "$pod" -c api -- \
  enterprise-doc-object-snapshot "${snapshot_args[@]}" --confirm

umask 077
kubectl -n "$namespace" exec "$pod" -c api -- cat "$remote_manifest" > "$local_manifest"
kubectl -n "$namespace" exec "$pod" -c api -- cat "$remote_record" > "$local_record"
manifest_sha256=$(python -c \
  'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["manifest_sha256"])' \
  "$local_manifest")
```

Restore only while `DATABASE__URL` points at the retained
`enterprise_doc_restore_...` database. Transfer the reviewed manifest over stdin, then
provide the isolated URL over stdin as well; neither credential nor URL belongs in argv,
the manifest, the sanitized record or shell history:

```bash
restore_id=<reviewed-lowercase-restore-id>
restore_database=enterprise_doc_restore_20260805t094423z
remote_restore_record=/dev/shm/${restore_id}-r2-restore-record.json
local_restore_record=/secure/off-repo/path/${restore_id}-r2-restore-record.json

kubectl -n "$namespace" exec -i "$pod" -c api -- \
  sh -c 'umask 077; cat > /dev/shm/reviewed-r2-snapshot.json' < "$local_manifest"

restore_args=(
  --manifest-path /dev/shm/reviewed-r2-snapshot.json
  --expected-manifest-sha256 "$manifest_sha256"
  --restore-id "$restore_id"
  --expected-database-name "$restore_database"
  --expected-endpoint-host "$endpoint_host"
  --allowed-bucket "$documents_bucket"
  --allowed-bucket "$artifacts_bucket"
  --record-path "$remote_restore_record"
)

read -rsp 'Isolated restore DATABASE URL: ' RESTORE_DATABASE_URL
printf '\n'
run_restore() {
  printf '%s\n' "$RESTORE_DATABASE_URL" | kubectl -n "$namespace" exec -i "$pod" -c api -- \
    sh -c 'IFS= read -r DATABASE__URL; export DATABASE__URL; exec "$@"' \
    sh enterprise-doc-object-restore "${restore_args[@]}" "$@"
}

run_restore
# Review endpoint, database, prefix, manifest digest and planned counts first.
run_restore --confirm
unset RESTORE_DATABASE_URL

umask 077
kubectl -n "$namespace" exec "$pod" -c api -- cat "$remote_restore_record" \
  > "$local_restore_record"
```

The confirmed restore verifies that isolated-database references, manifest entries and
the listed restored object set are equal in both directions. It never overwrites live
application keys. This proves the R2 snapshot/restore and DB/R2 integrity sub-gate only;
it does not make the application read from the isolated prefix. A separate temporary
application configuration and authenticated upload/ingestion/Agent smoke are still
required before claiming complete cross-system recovery, and a staging drill alone does
not establish a production RPO/RTO objective.

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
