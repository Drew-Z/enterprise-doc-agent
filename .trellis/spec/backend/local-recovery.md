# Private Local Recovery Drill

## Scope / Trigger

`scripts/local_recovery_drill.py` prepares database recovery on an existing local
Docker Compose PostgreSQL service. It is not a production rollback, object-store
restore, standby server, or independent-failure-domain acceptance.

## Signatures

`run_drill(root, compose_file, output_dir, source_database, restore_database,
postgres_user, keep_restore_database)` uses keyword-only arguments and returns a
local report. CLI requires `--output-dir`; without `--confirm-local` it only prints
the selected paths and names. Optional `--report-path` must be inside that directory.

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

## Good / Base / Bad Cases

Good: a fresh target restores a synthetic current-schema fixture, content hashes
and replay behavior are checked separately, and owned resources are removed.
Base: default CLI preview creates no files and opens no connections.
Bad: an operator supplies a previously used restore name; refuse to replace it.

## Tests Required

`tests/deployment/test_local_recovery_drill.py` exercises the public runner/CLI with
only subprocess replaced. Assert preserved old backups, no deletion of existing
or concurrently created databases, cleanup on failure/timeout, keep behavior,
artifact hashes, and preview inactivity. Real local evidence must state the
migrated revision, synthetic-data scope, script hash and resource cleanup.

## Wrong vs Correct

Wrong: `dropdb --if-exists` before restore, or a successful local inventory check
reported as a production recovery pass. Correct: exclusive creation, owned cleanup,
private centralized artifacts, and explicit external acceptance limitations.
