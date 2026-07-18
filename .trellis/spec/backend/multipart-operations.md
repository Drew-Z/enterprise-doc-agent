# Multipart Operations And Evidence

## Runtime Contract

- `scripts/multipart_smoke.py` owns its local Compose and API processes and never
  removes named volumes.
- Generated smoke content is deterministic UTF-8 TXT data. The client computes the
  declared whole-content digest incrementally, retains at most one multipart part in
  memory, and sends the bytes to the presigned object-store URL rather than FastAPI.
- The smoke uploads the configured number of leading parts, stops the API, starts a new
  API process, fetches reconciled server state, uploads only missing parts, completes,
  and repeats completion. The second completion must return the same durable document
  and version with `replayed=true`.
- RSS sampling observes the process that actually owns the API listening socket. This
  matters on Windows, where a virtual-environment launcher process may otherwise report
  a small working set while the child Python process serves requests.
- A successful report contains counts, sizes, timestamps, environment facts, RSS
  aggregates, and limitations only. It excludes tokens, authorization headers, signed
  URLs, session/object-store identifiers, filenames, and content digests.
- Raw API logs remain temporary on success because request paths contain resource IDs.
  They are retained only after failure for local diagnosis and are not evidence inputs.

## CI Boundary

- The `m1-integration` GitHub Actions job starts the pinned local PostgreSQL/MinIO
  profile, applies Alembic, and runs all multipart integration tests without skips,
  reruns, or allow-failure behavior.
- After integration services stop, the job runs a generated 17 MiB two-part smoke with
  one API restart and uploads its sanitized report.
- The CI payload is a fast regression gate. It does not satisfy or replace the separate
  reviewed-commit 1 GiB evidence requirement.

## Evidence Boundary

- Do not create an M1 passed manifest from a dirty worktree or a commit that does not
  contain the implementation being measured.
- First commit and review the implementation. Then run the exact quality, integration,
  browser, 1 GiB smoke, evidence-contract, and Trellis commands against that commit.
- Every referenced command log, report, and screenshot must be materialized beneath
  `evidence/m1/` and recorded with its SHA-256 digest. Earlier M0 evidence is immutable.
- One successful local 1 GiB run is not a load test, sustained-throughput result, or
  production capacity claim.

## Proven Examples

- `scripts/multipart_smoke.py`
- `tests/multipart/test_multipart_smoke_contract.py`
- `.github/workflows/quality.yml`
- `tests/foundation/test_ci_contract.py`
- `README.md`
