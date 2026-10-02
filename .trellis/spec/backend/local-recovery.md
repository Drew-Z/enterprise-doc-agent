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

## Full local VM execution findings

- Confirm hardware execution by booting the guest, not only by initializing the
  accelerator. This Windows host required `whpx,kernel-irqchip=off`. Preserve the
  failed default attempt and keep all QEMU helpers windowless.
- Use a fresh overlay over the verified base image. Freeze the selected verified
  snapshot at failure declaration; retain that snapshot and clock across repairs.
  Identify advance media preparation separately from measured rebuild time.
- Include implicit local-path BusyBox helpers in air-gap media. Running-system
  image inventories can omit them. Verify actual PVC binding and CRI image aliases
  before claiming services are ready; a helper script must exit nonzero on failed
  readiness instead of merely printing `not_ready`.
- Do not trust successful stdin transfer alone. A kubectl WebSocket transfer of a
  24 MB archive returned success while only 32 KiB reached the container. Compare
  exact byte count and SHA before pg_restore. A receiver such as `pg_restore --list`
  may exit without consuming its input; spool and verify the archive first, and
  generate the filtered archive list in the container without another bulk stdin.
- When copying through a local guest's container root, resolve the current PID
  from the exact owned, running container and refuse an existing destination.
  Retain interrupted files and hashes; never replace source or restored data to
  hide a mismatch. Keep all such staging bytes on the local recovery VM disk.
- The 2026-10-02 second cold run observed RPO 189.325 seconds and 883.364 seconds
  from fresh VM creation to historical API/Web verification, including repairs.
  The third run consolidated those repairs and automatically completed historical
  checks, a new Presales business flow and verifier cleanup in 353.051 seconds,
  with RPO 117.036 seconds. Keep both results and all earlier failures distinct.
- Before starting workers on a restored historical snapshot, explicitly verify
  active jobs, outbox and Presales counts. The third run required all three to be
  zero. It proves new controlled business, not safe recovery of historical active
  work or new Agent/MCP executions. Do not remove this precondition without testing
  leases, interrupted dispatches, reservations and duplicate delivery behavior.
- Excluding synthetic tenant IDs must preserve nullable historical ownership:
  use `tenant_id IS NULL OR tenant_id NOT IN (...)`. SQL `NOT IN` alone drops NULL
  rows and creates false fingerprint mismatches. The local probe verified all 43
  tenant-related tables, including existing browser session/event NULL rows.
- Recovery fixtures must satisfy existing entitlement timestamps and Chinese
  model prose contracts; do not weaken production validation to make fixtures pass.
  Count controlled requests and ledger consumptions independently, verify repeated
  reads/replays do not consume again, and restore API configuration after cleanup.
- Neither local run proves cloud/public cutover, production OAuth or suppliers,
  off-computer backups, or an on-call SLA. See `docs/ops/local-cold-recovery.md`
  for exact scope, advance media preparation and preserved failures.

## Encrypted recovery bundles

- Verify pinned age release archive size and official SHA-256 before extracting
  or executing binaries. A partial download is not an installed tool; preserve
  its failure and only reuse bytes after the complete archive hash matches.
- Keep the age private identity in the restricted centralized local recovery
  group. A server backup process needs only the public recipient. No remote
  sensitive-data storage exception is implied by having working encryption.
- Authenticate the entire ciphertext before consuming decrypted members. Verify
  an exact member inventory, each byte length and hash; reject duplicate names,
  path escapes, links, extra/missing files and over-budget bundles. Parsing a tar
  must not extract arbitrary paths to the filesystem.
- Bind database dump, content fingerprints and portable object mappings to the
  same verified source snapshot. Include every referenced object byte; referring
  to a Windows backup path is not a portable backup.
- Local evidence separately records encryption/readback and actual restore.
  The 2026-10-02 proof restored 56 tables/82,622 rows and 1,668 objects solely from
  ciphertext into empty owned PostgreSQL/MinIO, using tmpfs for plaintext staging.
  Its 17.531 seconds includes cleanup but is not a whole-machine RTO.
- Current bounded in-memory packaging caps the entire bundle at 64 MiB and 4,096
  payload members. Growth must fail visibly; a streaming design needs its own
  complete-authentication and corruption tests before increasing this boundary.
- Upload completion, ciphertext readback, source snapshot verification and actual
  decryption/restore are separate claims. Off-computer continuity additionally
  needs an independently running schedule and measured source-time freshness.
  See `docs/ops/encrypted-backup.md` for implementation status and remaining work.

## Server capture and publication validation

- Hold the exported repeatable-read, read-only transaction until both inventory
  and pg_dump complete. Derive table fingerprints and object references inside
  that snapshot. The local concurrent-write proof restored the snapshot's counter
  at zero while the source advanced 26 times; matching row counts alone is not proof.
- Native SDK compatibility must preserve the actual signed conditional write.
  botocore 1.34.46 rejects the IfNoneMatch keyword; a scoped before-sign event can
  insert `If-None-Match: *`, with unregister in finally. Never retry unconditionally.
- Persist sealed identity and ciphertext before upload. A process loss after
  remote readback but before local receipt resumes exactly that identity/bytes.
  Unknown uploads must not create another snapshot or replace the last success.
- Whole-container-set comparisons are unsafe when an independent scheduled job
  owns temporary containers. Preserve the original assertion failure, correlate
  exact owner/success/cleanup receipts, and separately verify stable resources and
  the restore's own cleanup. Empty Docker event history is not affirmative proof.
- Bound the complete managed-prefix inventory, pagination and byte budget before
  new publication. Count both ciphertext and completion markers; partial uploads
  consume space. Same-ID replay only reserves missing bytes. This assumes one
  namespace writer and the runtime process lock; it is not a distributed quota.
- Capacity exhaustion preserves sealed work and existing points. Retention is an
  exact reviewable plan, not implicit deletion authorization. Keep at least five
  distinct actually restored contents, plus pinned/referenced/failed/in-progress,
  incomplete and unverified snapshots. New unverified uploads cannot displace the
  five restored contents. Accept restore proofs only from a trusted operator
  catalog; an upload marker cannot assert its own successful restore.
- The native local test retained three snapshots / six objects (85,516,410 bytes),
  replayed at the exact cap, and refused a new ID without object or ETag changes.
  It did not test production R2, retention deletion, monitor integration, or
  continuity while the personal computer is offline. The latest actual data
  restore verified 56 tables / 82,705 rows and 1,669 objects; its 21.047 seconds
  remains a database/object drill, not whole-machine RTO.

## Tracked server operations package

- The promoted implementation is `scripts/server_backup`; retain the original
  private sources and execution evidence. Compare generated SQL and query ASTs
  when formatting/moving capture code, then rerun actual age authentication tests.
- Production entry uses `--require-production-config`. Reject unknown/test
  options, arbitrary credential commands, a changed namespace/path/prefix, and
  an unapproved transport shape. Read protected regular root-owned JSON files
  without symlinks or group/world permissions. Never log credential-adapter stdout.
- Package preparation pins each module and the Linux age ELF by size/SHA, and
  binds configuration to that inventory. First installation refuses an existing
  root or unit and stages without credentials or startup. Preserve partial
  failures; do not silently replace them. Rollback checks the exact owned unit
  hash before stopping it and retains all files/backups.
- The actual native staging proof uses production paths in the owned local VM,
  a synthetic target and no capture. systemd syntax, strict configuration,
  credential-file permission rejection, installed hashes and disabled rollback
  passed. It does not prove production credentials, provider connectivity,
  off-computer continuity or a trusted restore/protection catalog.

## Operator catalog evidence boundary

- `scripts/server_backup/restore_catalog.py` authenticates the complete local
  bundle before deriving content identity. Hash canonical table fingerprints,
  object mappings/references/hashes, schema/extensions and release metadata;
  exclude capture timestamps, randomized ciphertext and incidental dump bytes.
- Import actual restore receipts only with operator-selected receipt/executor
  hashes anchored in the protected recovery registry. A hash pins identity, not
  execution; do not obtain the expected hashes from uploader-controlled markers.
  Keep the raw original receipt, its overall status and any separate audit.
- Failed/unknown attempts remain protected even with an earlier successful
  attempt. Missing proofs, pinned snapshots, in-progress work and every explicit
  owner reference protect the named snapshot; unknown references stay visible.
- Validate the exact catalog SHA and all receipt/source bindings before retention
  planning. The local operator owns catalog completeness and updates; this module
  neither discovers external owners nor authorizes or executes deletion.
- Real validation found three native ciphertexts with identical logical contents;
  all stay protected because none has an unqualified actual restore receipt.
  Preserve the `original_container_state_changed` result. A new module changes
  the package ID and does not retroactively extend an older native install proof.

- Verify vendor endpoints against official documentation and actual DNS/provider
  metadata before production preparation. The initial R2 fixture repeated the
  incorrect `.r2.storage.cloudflare.com` in implementation and tests. The corrected
  default-jurisdiction endpoint is `.r2.cloudflarestorage.com`; a positive official
  endpoint and explicit typo rejection now prevent that self-consistent mistake.
  A different bucket on the same R2 authority is not an independent account or
  provider failure domain. Temporary credential action/prefix enforcement must
  be tested live; a directory of available permission groups proves no issuance right.

## Scenario: Temporary R2 publisher credentials

### 1. Scope / Trigger

Production backup publication and same-ciphertext retry must acquire fresh scoped
sessions without relying on a personal computer for renewal.

### 2. Signatures

`publication_credentials(parent, *, now=None)` issues the session;
`validate_session(value, *, now=None)` validates its local contract.
`storage(endpoint, access, secret, region="auto", *, session_token=None)` passes
`aws_session_token` to the real boto3 client and explicitly selects SigV4.

### 3. Contracts

The root-owned parent JSON has exactly `endpoint`, `bucket`, `access`, `secret`,
and `region`; the key lengths are 32/64 lowercase hex characters, region is `auto`,
and the endpoint uses the official default-jurisdiction account hostname.
Sign HS256 with the UTF-8 secret string, not decoded hex. Bind account, issuer,
audience and bucket; fix TTL to 900 seconds, prefix to `operations-recovery/v1/`,
and actions to ListObjectsV2/HeadObject/GetObject/PutObject. Do not also include
`scope`: live R2 rejected that vendor-example combination with InvalidArgument,
while the same locally signed payload with actions alone was accepted. Child secret is
SHA-256(compact JWT); session token is base64(`jwt/` + compact JWT).
The adapter never returns the parent secret. Re-sign for each publication/retry.
Local decoding checks the contract; only R2 authenticates/enforces the token.
Parent issuance is separate and must restrict the parent to the selected bucket.

### 4. Validation & Error Matrix

- Missing/expired session or mismatched account/bucket/actions/prefix -> reject
  before S3 construction; never fall back to the parent key.
- Extra parent configuration, non-default region or malformed key -> reject.
- Group/world-readable parent, symlink or non-root ownership -> reject.
- R2 authentication/authorization failure -> preserve existing runtime retry and
  sealed snapshot state; do not infer an absent upload from an unknown result.

### 5. Good/Base/Bad Cases

Good: restart after expiry obtains a new session for the original sealed upload.
Base: synthetic keys prove signing/SDK plumbing without target network calls.
Bad: a broad operations token is installed because dedicated token issuance is
unavailable; or local claim validation is described as actual R2 enforcement.

### 6. Tests Required

`test_server_backup_credentials.py` independently verifies signatures with PyJWT,
checks actual botocore presigned session-token/SigV4 fields, renewal, unchanged
parent and rejection before S3. The native Linux adapter/SDK proof uses the same
module hashes, an exclusively owned synthetic file and inactive service. Live R2
separately verified immutable publication/readback/replay, HEAD/LIST and renewal,
plus 403 rejection of deletion, out-of-prefix operations and an expired token.
Keep the failed overall receipt when later cleanup requires separate recovery;
link exact original hashes to the supplemental deletion/absence receipt.

### 7. Wrong vs Correct

Wrong: return the parent secret or declare cloud permission enforcement from a
decoded JWT. Correct: mint the fixed child, pass its session token to the SDK,
verify provenance and enforce the real R2 boundary before production activation.
