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
