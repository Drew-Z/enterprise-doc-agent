# Private Local Recovery Drill

## Scope / Trigger

`scripts/local_recovery_drill.py` prepares database recovery on an existing local
Docker Compose PostgreSQL service. It is not a production rollback, object-store
restore, standby server, or independent-failure-domain acceptance.

`scripts/local_object_recovery.py` adds a separate source-read-only object capture
and loopback-only restore. Its result proves the verified objects, not a complete
application restart or an external recovery SLA.

## Signatures

`run_drill(root, compose_file, output_dir, source_database, restore_database,
postgres_user, keep_restore_database)` uses keyword-only arguments and returns a
local report. CLI requires `--output-dir`; without `--confirm-local` it only prints
the selected paths and names. Optional `--report-path` must be inside that directory.

`capture_objects(client, references, output_dir, allowed_buckets, max_objects,
max_total_bytes, workers=1, timeout_seconds=600)` takes keyword-only arguments.
`restore_objects(client, manifest_path, expected_sha256, allowed_buckets,
workers=1, timeout_seconds=600)` also takes keyword-only arguments. Callers supply
S3 clients with bounded connect/read timeouts and retry counts. Worker count is
1–4 and the deadline cannot exceed 1800 seconds; blocking requests are bounded by
the client timeouts. These are Python functions, not an implicit live CLI.

## Contracts

- Output is a new directory outside the repository. In Codex operations, choose a
  directory within the current centralized task recovery group and register its
  artifacts in that group's manifest. Never reuse an existing dump directory.
- Source/restore names are simple PostgreSQL identifiers; restore is distinct and
  begins with `enterprise_doc_restore_`.
- Preflight rejects an existing target. Exclusive `createdb` is the final race
  protection. No `dropdb` occurs before creation. Cleanup in `finally` applies only
  after creation succeeds, unless `--keep-restore-database` was chosen. A failed or
  timed-out create has uncertain ownership and is never followed by automatic drop.
- Text/admin commands have 60-second deadlines; dump/restore have 300-second
  deadlines. No shell expansion or credential arguments are introduced.
- `artifact_scope=local-private` uses absolute local paths and SHA-256 hashes.
  Dumps and inventories stay local; publish only sanitized summaries. POSIX modes
  restrict the directory to 0700 and files to 0600; Windows requires an appropriately
  restricted parent ACL because `chmod` does not implement those ACLs.
- The report stays `blocked_external`. Table counts/revisions are labeled
  `database_inventory`, not content correctness. Backup age is measured at restore
  start. Local timings do not become production RPO/RTO measurements.
- Object capture only calls source GET, groups identical locations and checks
  every reference's size and SHA. Conflicting metadata, duplicate reference IDs,
  empty inventories and exceeded budgets fail before source I/O. Filenames are
  SHA-256 of `bucket + NUL + key`; bucket/key and reference IDs stay in private
  `objects.json`. The success manifest is published only after all downloads pass.
- Object restore checks the actual client's endpoint hostname (`127.0.0.1` or
  `::1`), the manifest SHA and every local blob before its first write. It rejects
  path escapes, symlinks and nonempty target buckets; conditional puts use
  `IfNoneMatch="*"`. Each restored object is read back and hashed. The caller must
  create a dedicated local target, compare the final inventory to the restored DB,
  and clean only resources it owns. No source bucket writes or reference remapping
  are needed when a separate local MinIO preserves the original keys.
- Failed captures retain partial artifacts and have no success manifest. There is
  no implicit resume or overwrite option. An operational resume must separately
  verify every reused file against the same restored DB snapshot, record request
  limits and failures, and retain the initial failure evidence. Never rerun against
  an existing directory as if it were a fresh successful capture.

## Validation & Error Matrix

| Condition | Result |
| --- | --- |
| Output inside repository / already exists | Reject before DB commands |
| Target exists during preflight | Reject before output creation or dump |
| Concurrent target creation | `createdb` fails; no cleanup of another target |
| Restore fails / times out / inventory differs | Fail, retain private evidence, clean owned target unless kept |
| Cleanup fails | Fail; retain names and command log for manual inspection |
| Explicit keep mode | Leave owned target for inspection; caller records exact later cleanup |
| Matching inventory | Return `blocked_external`; no object/application acceptance inferred |
| Object bytes truncated, too long or wrong SHA | Fail; do not publish a snapshot manifest |
| Local manifest/blob tampered or missing | Fail before any restore writes |
| Remote endpoint / nonempty local bucket | Refuse to restore; preserve existing objects |
| Restore readback differs | Fail; retain evidence and owned target for caller cleanup |

## Good / Base / Bad Cases

Good: a fresh target restores a synthetic current-schema fixture, content hashes
and replay behavior are checked separately, and owned resources are removed.
Base: default CLI preview creates no files and opens no connections.
Bad: an operator supplies a previously used restore name; refuse to replace it.

For objects, good is a DB-bound capture followed by an empty loopback target and
exact byte verification. Base is a budget-checked capture with a restricted output
parent. Bad is treating an R2-to-R2 server-side copy as a local-only operation.

## Tests Required

`tests/deployment/test_local_recovery_drill.py` exercises the public runner/CLI with
only subprocess replaced. Assert preserved old backups, no deletion of existing
or concurrently created databases, cleanup on failure/timeout, keep behavior,
artifact hashes, and preview inactivity. Real local evidence must state the
migrated revision, synthetic-data scope, script hash and resource cleanup.

`tests/deployment/test_local_object_recovery.py` exercises capture and restore
through a fake S3 transport: source writes never occur, shared references read once,
budget/metadata failures precede I/O, all local blobs are checked before writes,
and failures, overwritten targets or corrupted readback cannot return success.

For real DB comparison, share a PostgreSQL exported repeatable-read snapshot
between source inventory and `pg_dump`. Do not compare a changing live table with
a previous dump without binding the source state. Record client/server/extension
versions. JSON text of a floating-point column can differ between PG minor versions
while its IEEE bytes are unchanged. The 2026-09-25 drill found this for
`agent_run_evidence.rrf_score` (17.6 → 17.10): retain the initial failure, verify the
same SELECT still reproduces the original source snapshot fingerprint, and compare
`float8send(rrf_score)` plus all remaining columns. Do not round, drop the column,
or equate matching row counts with content correctness.

## Wrong vs Correct

Wrong: `dropdb --if-exists` before restore, or a successful local inventory check
reported as a production recovery pass. Correct: exclusive creation, owned cleanup,
private centralized artifacts, and explicit external acceptance limitations.

## Proven Examples

- `scripts/local_recovery_drill.py`
- `scripts/local_object_recovery.py`
- `tests/deployment/test_local_recovery_drill.py`
- `tests/deployment/test_local_object_recovery.py`
- `docs/ops/single-node-operations-acceptance.md`
