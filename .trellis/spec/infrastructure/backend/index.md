# Infrastructure Backend Context

Use the database and quality guides in `.trellis/spec/backend/`. Local M0
infrastructure is owned by `infra/compose`; application images and production
deployment remain outside M0.

`infra/identity` has a separate [identity/mail contract](./identity-mail.md) for
the real local Keycloak lab and private Cloudflare SMTP/API adapter. Its captured
mail evidence does not establish public delivery or production hosting.

`infra/cloudflare_mail` follows the [private mailbox package contract](./cloudflare-mail.md)
for pinned source builds, fail-closed secrets, receive-only defaults and real local
Worker/D1/browser acceptance. Public provisioning, explicit recovery, Windows
credentials and live HTTPS/browser checks follow the separate
[mailbox rollout contract](./cloudflare-mail-rollout.md).

Commercial candidate evidence aggregation and the read-only manual CI gate follow
the [commercial readiness contract](../../backend/commercial-readiness.md). Mechanical
evidence completeness does not authorize deployment or replace actual customer review.

The [private local recovery contract](../../backend/local-recovery.md) governs
explicit backup output, isolated database ownership, cleanup and local-only reports.

The [local business capacity contract](../../backend/business-capacity.md) governs
bounded workflow sampling, per-operation accounting, cancellation denominators and
the local CLI. Functional sampling does not establish current 4C4G capacity.

The [external monitor contract](./external-monitor.md) covers independent Cloudflare
readiness scheduling, D1 notification deduplication, unknown mail outcomes and
explicit receipt verification. It does not replace queue/backup or business SLOs.

The [image cache runtime contract](../../backend/image-cache-runtime.md) covers
containerd/CRI import aliases, exact digest checks and bounded in-window repairs.
Successful image listings do not alone establish container startup readiness.

On schema `20261005_0032`, `ReleasePlan` defaults to the existing same-image upload
flag switch. A new image release must explicitly declare `release_kind=images_only`;
configuration, credentials, pool settings, unrelated approvals and workload specs stay
fixed. Both candidate and recovery image digests must be approved. Schema drift must
reject the operation before writes; partial execution restores the original full specs.
This mode performs no migration and does not permit a return to schema 0031. Bind and
verify the exact executor source independently from the signed application source.
