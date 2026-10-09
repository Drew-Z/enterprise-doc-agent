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

## Schema 0037 review-citation publication and rollback

1. Scope: fixed0036-to-0037 expansion retains original applications; a separate
   image-only window introduces per-review citation readers. Preserve every earlier
   review, draft, human author, workbook and attempt.
2. Signatures: ExpansionPlan schema4 binds exact migration/executor hashes and unchanged
   workbook/manual reader booleans. PsqlSession(citations=True) is mutually exclusive
   with manual/workbook selection and checks inherited shapes plus nullable/default-free
   citations JSONB and its exact validated array/length check.
3. Contract: retain the same credential-private session/advisory lock, verified limits,
   deadlines and atomic transaction. Lost receipts reconcile complete0036/0037 without
   DDL replay. Recovery checks inherited reader compatibility and refuses any nonnull
   citation history before writes and before reopening original applications.
4. Image validation: schema0037 requires images_only and exact boolean original/candidate
   workbook_readers, manual_readers and citation_readers. Apply checks both sides; restore
   original. Before writes and after applications stop, each incompatible capability
   requires empty corresponding history. Configuration/credentials/specs remain fixed;
   idle checks protect both rollback boundaries.
5. Cases: good = rc48 rollback before any independent review citations, or compatible
   readers with history. Bad = rc48 after an explicit array, including[]. SQL null alone
   retains original-draft binding. Never erase a revision to enable recovery.
6. Tests: exact Alembic SQL, source/scope/capability drift, all three history close races,
   real PostgreSQL atomic failure/lost receipts/schema drift and actual corrected review,
   original draft/earlier review/workbook/CSV/accounting preservation through refused or
   compatible recovery. Owned fixtures clean their schemas in finally blocks.
7. Local verification precedes exact signed publication and live startup/rollback checks.
   The rc48 drill must precede citation history; afterward recover with compatible images
   or a forward fix. Human evidence correction does not establish model accuracy.

## Schema 0036 human-authorship publication and rollback

1. Scope: fixed0035-to-0036 expansion retains original applications; a separate
   coordinated image-only window introduces manual-author readers. No history deletion.
2. Signatures: ExpansionPlan schema4 binds the exact manual migration/source digests
   and unchanged boolean workbook_readers. PsqlSession(manual=True) checks all inherited
   shapes and the nullable/default-free manual_authorship JSONB with its exact validated
   draft/revision check. Manual and workbook migration selectors are mutually exclusive.
3. Contract: same private session/advisory lock, verified limits and atomic transaction.
   Lost receipts reconcile complete0035/0036 without DDL replay. Expansion recovery
   refuses any manual history before writes and before reopening original applications.
4. Image validation: schema0036 requires images_only and exact boolean original/candidate
   capabilities for both workbook_readers and manual_readers. Before writes and after
   all applications stop, each incompatible capability requires its history to be empty.
   Apply checks both sides; restore checks original. Preserve all workload/credential
   and configuration restrictions, including idle checks at both rollback boundaries.
5. Cases: good = initial rc47 rollback before any human record, or compatible-reader
   recovery with records. Bad = rc47/rc46 after human history: unchanged draft JSON parses
   but loses correct attribution. Never erase authorship to make rollback possible.
6. Tests: actual Alembic SQL equality, plan/resource/capability drift, both close races,
   owned PostgreSQL atomic failure/lost receipts/exact constraint drift, and actual
   reviewed human response plus XLSX/CSV retention through compatible/refused recovery.
7. Local checks do not prove deployed startup. Verify exact signed images and exercise
   rollback before live manual fixtures. Keep frozen public samples and failed results.

## Schema 0035 workbook publication and rollback

1. Scope: extend the fixed expansion executor with exactly 0034-to-0035; image
   switching remains a separate operation. Neither window removes workbook history.
2. Signatures: `ExpansionPlan` retains schema version 4 and exact SQL/source hashes.
   `PsqlSession(workbook=True)` validates both old JSONB fields plus the new nullable
   JSONB/bytea pair and its exact validated size/pair constraint. `ReleasePlan` on
   0035 requires `images_only` and boolean `workbook_readers.original/candidate`.
3. Contract: use the same session advisory lock and deadlines for both fixed
   expansions. Keep all original resources during migration. Lost receipt recovery
   reconciles one complete schema; never resend DDL or downgrade to erase an import.
   Reader capabilities must be bound to the reviewed signed image digests in the plan.
4. Validation: a legacy reader requires no saved workbook before writes and again
   after every application stops. Apply checks both candidate and recovery readers;
   restore checks the original reader. Active work, unknown revision, partial schema,
   configuration changes or ambiguous capability values refuse the operation.
5. Cases: good = initial deployment and rc.45 rollback before imports, or switching
   between compatible readers with saved imports. Base = 0032/0034 contracts unchanged.
   Bad = returning to rc.45 after an import or deleting history to make that possible.
6. Tests: fixed SQL versus real Alembic output; resource/idle race checks; real owned
   PostgreSQL atomic DDL failure, lost receipts, exact shape drift and 13-row workbook
   preservation after a refused legacy rollback. Existing expansion regressions remain.
7. Wrong/correct: a readiness response does not establish workbook-reader compatibility.
   After an import, retain a compatible image or deploy a forward fix. Local tests are
   separate from signed-image startup, supervised live rollback and public file replay.

## Schema 0034 image-only release contract (local candidate)

1. Scope: apply or restore already-reviewed application images on an already-expanded
   `20261008_0034` database. This entry never performs the 0032→0034 migration.
2. Signatures: existing `ReleasePlan` schema version 2, exact `original_revision`,
   explicit `release_kind=images_only`; existing `ReleaseCluster.apply/restore`.
3. Contract: keep configuration, credentials, pools, concurrency and all unrelated
   workload fields. Bind approved candidate/rollback image digests and source hashes.
   Preserve 0033/0034 columns and all historical policies/review metadata.
4. Validation: other modes on 0034 and live revision drift reject before writes.
   Restore requires idle business before any mutation and again after every application
   is stopped. Work arriving during close blocks rollback without opening an old Worker.
5. Cases: good = drained full/partial image rollback; base = unchanged 0031/0032 modes;
   bad = hand a new-policy task to rc.40, change config, or downgrade history.
6. Tests: `test_release_switch.py` covers exact schema, scope expansion, complete/partial
   restore and both idle-check boundaries. `test_presales_release_compatibility_integration.py`
   executes frozen rc.40 GET/projection/schema against new database content, asserting
   full readable old fields, two review revisions, tenant denial and unchanged metadata.
7. Wrong/correct: changing a plan revision is not migration. Verify an independently
   completed expansion first; use this no-migration executor only on exact 0034. Frozen
   reader compatibility does not replace signed-image startup or actual rollback evidence.

Independent post-release observation compares the full five workload specs with the
candidate, then recomputes the cache workload digest and required references from those
same specs. Keep node/boot/namespace, filesystems and eviction-policy bindings unchanged.
Verify packaged Python against the signed application commit, separately from the external
executor commit, and compare served Web assets with the running signed image. Count only
nonterminal Pods as active replicas; retain and compare historical Failed/Succeeded Pod
identities separately. Historical failures must neither be deleted nor counted as extra
current replicas. A stopped transient unit may have been garbage-collected: its default
not-found properties are not evidence of the limits used while it ran.

## Fixed 0032-to-0034 expansion (local candidate)

1. Scope: the separate `scripts.presales_schema_expand` window expands only the two
   nullable JSONB columns/checks while retaining every original rc.40 resource.
2. Signatures: `ExpansionPlan` schema version 4, `presales_schema_expand` kind, exact
   original/target revisions and fixed migration digest; CLI validate/arm/execute/status,
   exact plan SHA and seven source-module hashes. No arbitrary SQL or workload delta.
3. Contract: `PsqlSession` owns one credential-private psql connection and a session
   advisory lock throughout apply/restore. Version, exact column/constraint shape,
   idle checks and the single migration transaction use that connection. All CLI
   calls are bounded; database stdout is capped at 64 KiB and raw stderr is discarded.
   Both revisions commit atomically; recovery never downgrades or resends the migration.
   Before acquiring the lock, explicitly SET statement_timeout=10000ms,
   lock_timeout=5000ms, idle_session_timeout=600000ms and the exact search_path.
   Read pg_settings millisecond values and current_schema back on that connection;
   reject missing, malformed or differing values. PGOPTIONS alone is insufficient:
   the live session pooler ignored it. Setup, readback and lock acquisition share
   one connect_timeout deadline, including on recovery.
4. Validation/errors: unknown/0033 revisions, partial columns, missing/wrong constraints,
   pending business or resource drift reject. Lost acknowledgement closes the old
   session; recovery must acquire the same lock before cluster writes. Lock failure
   is a blocked recovery, never proof that an earlier transaction was not committed.
5. Cases: good = complete expansion, or original-app recovery on complete 0032/0034;
   base = older Plan/ReleasePlan still accept only their single exact revision;
   bad = launch old recovery while another database session may still commit DDL.
6. Tests: deployment tests compare fixed SQL with actual offline Alembic output,
   reject scope/source drift and verify cluster ordering/idle races. Real PostgreSQL
   tests cover rollback at the last version update, missing receipts before/after
   COMMIT, advisory-lock contention, schema drift, pending Jobs, output/time bounds,
   and Switch recovery after a committed migration plus lost application-start reply.
   Test schemas and explicit temporary state directories are removed in finally/context exits.
   Also override startup options at the subprocess boundary against real PostgreSQL
   and assert effective limits/schema. A process boundary returning bad settings must
   be closed with a credential-free error before the window can begin.
7. Wrong/correct: a read on another connection cannot fence a late COMMIT. Acquire
   the same session lock first, reconcile a known complete schema, then restore original
   applications under unchanged deadlines. Actual signed-image startup and live
   migration/rollback remain required; local process/Kubernetes fixtures do not prove them.

On schema `20261005_0032`, `ReleasePlan` defaults to the existing same-image upload
flag switch. A new image release must explicitly declare `release_kind=images_only`;
configuration, credentials, pool settings, unrelated approvals and workload specs stay
fixed. Both candidate and recovery image digests must be approved. Schema drift must
reject the operation before writes; partial execution restores the original full specs.
This mode performs no migration and does not permit a return to schema 0031. Bind and
verify the exact executor source independently from the signed application source.

For a combined application and presales inference release on schema 0032, explicitly
declare `release_kind=presales_inference`. Only `PRESALES__PRIMARY_REASONING_EFFORT`
and `PRESALES__PRIMARY_STREAMING` may change, including removal to restore inheritance;
at least one must change. Signed/approved application image changes are allowed.
Only image, configuration and prerequisite fingerprints may change in approvals.
Shared model routes, reasoning, streaming, all budgets, credentials, pool settings,
upload settings and unrelated workload fields remain fixed. Invalid override values,
implicit modes and schema drift are rejected before writes. Partial application
restores the complete original configuration and workload specs. Tests exercise the
public ReleasePlan and ReleaseCluster boundaries, including schema drift before writes.

Schema 0032 also accepts explicit `release_kind=reasoning_only`: only existing valid
primary and/or fallback reasoning efforts may change, with at least one actual change.
Only configuration and prerequisite approval fingerprints may change. Images, route
settings, credentials, budgets, streaming, pools and other workload fields stay fixed.
The same schema-drift checks and full-spec rollback apply. This release capability
does not establish model quality or authorize promoting an unreviewed candidate.
