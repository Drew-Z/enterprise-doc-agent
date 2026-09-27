# Staging Recovery After a Failed Deployment

## 1. Scope / Trigger

The small single-node deploy workflow pauses workloads for migration and
embedding work. Its final recovery must never become a second, unguarded deploy
path after prerequisites, migration or workload application failed.

## 2. Signatures

The `Restore tiny workloads after deployment attempt` step in
`.github/workflows/deploy-staging.yml` executes Bash with these inputs:

- `DEPLOYMENT_PROFILE`: only `single-node-4c4g` and `tiny-single-node` resume.
- `PREREQUISITES_OUTCOME = steps.prerequisites.outcome`.
- `MIGRATION_OUTCOME = steps.migration.outcome`.
- `WORKLOADS_OUTCOME = steps.workloads.outcome`.
- `RUNNER_TEMP/staging-workloads.yaml`: already rendered candidate workloads.
- `DEPLOYMENT_MODE = vars.STAGING_DEPLOYMENT_MODE || 'standard'`: maintenance always withholds resumption, including after successful migration/application.

`maintenance_guard.py` exposes `arm`, `supervise`, `pause-web`, `pause-backends`,
`stage`, `claim`, `status`. All take `--state`; all except status also take
`--plan` and `--plan-sha256`. Arm accepts bounded `--timeout` (default/max 1500s)
and `--recovery-budget` (default/max 300s). Claim additionally takes `--operation`,
`--executor` (40 lowercase hex), `--namespace-uid`, `--candidate` (full rendered
YAML). Status prints private JSON with phase/target/timing; refusal exits nonzero
with a sanitized message. Phases: armed, migration_claimed, recovering, restored,
blocked. There is no reset, extend, unclaim or automatic downgrade command.

The migration step binds `MAINTENANCE_GUARD_STATE`, `MAINTENANCE_GUARD_PLAN`,
`MAINTENANCE_GUARD_PLAN_SHA256`, `MAINTENANCE_OPERATION` from the corresponding
`STAGING_` environment variables; executor is `GITHUB_SHA`, Namespace UID is
`vars.STAGING_NAMESPACE_UID`. `MAINTENANCE_GUARD_DATABASE_URL` belongs only to
the administrator supervisor environment, not the deployer or plan JSON.

## 3. Contracts

Keep `if: always()` so the decision can be reported after failed attempts.
Before any `kubectl`, require all three outcomes to equal `success`. Missing,
unknown, cancelled, skipped or failed outcomes withhold candidate recovery.
An absent manifest and other profiles also produce no cluster commands.

The staging environment owns the explicit `standard`/`maintenance` mode so the
existing ten dispatch inputs remain compatible. Reject unknown modes; maintenance
requires `single-node-4c4g` and `run_smoke=false`. It is an installation while paused,
not a business rollout. Default standard behavior and supplier gates are preserved.

Before a maintenance migration, `scripts/staging_maintenance.py prepare` compares
all vector configuration, checks the four application Deployments have zero spec
and status replicas and rejects active namespace Pods except Redis. It deep-copies
only those four Deployments to replicas zero; Redis/PVC/Secrets are not applied.
After application, `verify` checks the same source/workload hashes, vector values,
installed image digests and paused state. Any failure leaves business stopped.

Maintenance additionally requires a detached, host-local supervisor before the
first pause. `maintenance_guard.py` binds the operation, executor SHA, Namespace
UID and plan hash in a create-only SQLite window. Its deadline uses Linux
CLOCK_BOOTTIME plus boot ID; wall clock changes do not extend the window. The
supervisor heartbeats at most every two seconds; a claim requires age <=10s.
BEGIN IMMEDIATE serializes pre-migration administrative mutations and migration
claim against timeout recovery. The real migration run body claims before its
first scale/delete/apply, verifying the complete rendered candidate hash and
prerequisites against the approved plan. Default standard mode is unchanged.

Never implement check-then-restore in a separate lock domain. Once recovering
wins, late pause/stage/claim must fail. Once migration_claimed wins, automatic old
runtime restoration is forbidden even if the job was never observed starting.
Recovering is committed before external actions; same-boot restart revalidates
partial configuration/restoration under the original total budget. Changed boot,
unknown DB revision, template/UID drift, job/controller drift or failed recovery
means blocked, never restored. Missing/corrupt SQLite state must fail closed.

The fixed cluster adapter restores only original ConfigMap data, Namespace
approval annotations and original application replicas, using resourceVersion
tests. It reuses validate_staging_prerequisites for the entire inventory; a
config-only administrator update is insufficient. It never rewrites workload
templates or performs downgrade. DB revision queries are read-only and bounded,
using the existing `postgres_process_environment` adapter and psql with
`--no-psqlrc --no-password --quiet --set=ON_ERROR_STOP=1`, a READ ONLY transaction,
2s statement timeout and 5s connection timeout. Credentials remain in the
supervisor environment only, never argv; no new runner Python driver is needed.
Backend readiness precedes
Web. Every external command and the entire recovery share bounded deadlines.
This trusted-operator coordination is not a new RBAC authorization boundary.

Maintenance skips embedding, browser and business smoke plus ordinary rollout
waits. The separate maintenance record is always `blocked_external` or `failed`,
never ordinary release `passed`; missing checks, mismatched hashes or unexpected
supplier-step outcomes fail. Pre-window write suspension, drain, consistent backups,
finite quota approval and the subsequent resumption plan are still required.

After successful prerequisites/migration/application, reapply the same candidate
and wait for API, Worker, Consumer and Web. Worker timeout is 1800 seconds for
4C4G and 600 for tiny; the other waits remain 600 seconds. Process failures
propagate; later commands must not run after a failed apply/wait.

This restores candidate replicas after a later pause; it is not an old-image or
database rollback. Failure before migration may require restoring the previous
deployment after inspection. Failure during migration leaves maintenance in
place until the actual database state is known.

## 4. Validation & Error Matrix

| Inputs / boundary | Result |
| --- | --- |
| Supported profile, three successful outcomes, existing manifest | Apply once, then four rollout waits |
| Any failure/cancelled/skipped/empty/unknown outcome | Exit zero from cleanup, no cluster commands; original workflow failure is retained |
| Other profile or missing manifest | No cluster commands |
| Apply or wait returns nonzero | Step returns that failure; later commands do not execute |
| Database migration state unknown | No automatic old/candidate image switch; inspect before recovery |
| Maintenance mode, including successful prerequisites/migration/application | No recovery commands; business remains stopped |
| Vector drift, missing/unknown workload, nonzero replica or active non-Redis Pod | Refuse before maintenance migration/application |
| Post-apply image/configuration drift, active Pod, or incomplete record outcomes | Maintenance verification/record fails; no business readiness claim |
| Missing/stale/expired guard, wrong operation/executor/UID/plan/candidate | Migration fails before any cluster command |
| Timeout recovery wins against migration | All late administrative writes and migration claims rejected |
| Migration ownership wins, including cancellation/unknown result | No automatic old-runtime restoration |
| Reboot, unapproved drift, changed/unknown revision, recovery failure/budget | Block; preserve evidence and require inspection |

## 5. Good/Base/Bad Cases

Good: after successful migration and application, embedding temporarily paused
API/Web; final recovery restores the same candidate and checks all workloads.

Base: rendering failed and outcomes are empty; the final step does not apply an
unvalidated manifest even if a file happens to exist.

Bad: migration failed while replicas were zero; unconditional `always()` plus
`kubectl apply` would start code against an unknown schema.

## 6. Tests Required

Execute the actual checked-in workflow `run` body with Bash, replacing only the
`kubectl` process boundary with a command recorder/failure injector. Bind and
assert its real environment expressions so changes in workflow wiring cannot
be hidden by a separate test-only decision implementation.

Cover every non-success outcome for all three prerequisites, successful recovery
on both small profiles, other profiles, absent manifests and failure propagation.
Tests must not contact a cluster. Windows uses the explicit installed Git Bash
path because the code under test is a Linux Bash workflow, never PATH `bash.exe`
or WSL. Local production commands otherwise remain PowerShell.

Execute the actual maintenance preparation, migration, application and verification
shell bodies with the real Python CLI and only `kubectl` replaced at the process
boundary. Cover both success and migration failure; refuse vector changes and
active processes, and retain skipped supplier validation in the outcome record.

Guard tests additionally exercise real SQLite transactions under competing
threads, a detached process that outlives its launcher, late/missing/stale/wrong
target CLI rejection before any kubectl, migration-claimed cancellation, same-boot
partial recovery, corrupt/unknown state, Namespace/ConfigMap/template/DB drift,
resourceVersion preconditions and backend failure leaving Web closed. Only OS
clock, cluster processes and database transport are substituted; the workflow
Bash, guard CLI, state persistence and full prerequisite validator are real.

## 7. Wrong vs Correct

Wrong: `always()` unconditionally reapplies `staging-workloads.yaml` and treats
successful health waits as evidence that migration passed.

Correct: retain the original step outcomes, require all three successes before
any cluster mutation and keep version-compatible recovery a separate operation.

## Proven Examples

- `.github/workflows/deploy-staging.yml`
- `tests/deployment/test_staging_recovery.py`
- `tests/deployment/test_staging_maintenance.py`
- `scripts/staging_maintenance.py`
- `scripts/maintenance_guard.py`
- `scripts/maintenance_guard_cluster.py`
- `tests/deployment/test_maintenance_guard.py`
- `tests/deployment/test_maintenance_guard_cluster.py`
- `docs/ops/maintenance-deployment-runbook.md`
- `docs/ops/commercial-rollout-plan.md`
