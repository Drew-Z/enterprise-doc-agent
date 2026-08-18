# Local And Server Progress Sync 2026-08-16

## Source Checkpoint

- Canonical local repository: `D:\workspace4Cursor\enterprise-doc-agent`.
- Frozen server source checkout: `/home/ubuntu/workspace/enterprise-doc-agent`.
- At the synchronization checkpoint, both checkouts were clean on
  `agent/release-scope-and-claude-server` at
  `6a0ae11628ff1842b7f03024e002ea5454d6074c`.
- The server's previous `main` revision, `a44038c`, was an ancestor of the synchronized commit.
  No server-only commit or uncommitted source change had to be recovered.
- The remote feature branch also pointed to `6a0ae11` at the checkpoint. Remote `main` pointed to
  `2f33a22`; the scope-decision commit had not been merged into `main`.

## Development Boundary

Server-side Claude Code development is disabled. The server source checkout is a frozen reference
and must not receive subsequent work-in-progress changes. New source work continues only in the
canonical local repository unless the service owner explicitly changes this boundary.

The source checkout revision is not the deployed runtime identity. The latest retained immutable
staging evidence binds the running release to commit `9131632f54dfdad42562f1d847036a14d47eb19c`
and its recorded image digests. A source synchronization does not by itself deploy those sources or
replace the running images.

## Local Continuation After The Checkpoint

The local branch intentionally advanced beyond the frozen server source with:

- `f18a855` — normalize the approved release-scope gate to the common manual-gate contract.
- `f3171b3` — render and protect an optional reviewed OpenAI-compatible fallback route.

The fallback slice keeps both model API keys in `enterprise-doc-secrets`, writes only the fallback
provider URL and model name to the non-secret ConfigMap, rotates the backend config hash, protects
the Namespace approval annotations, and rejects partial route configuration.

Full local quality passed after these changes:

- Ruff format and lint passed.
- Mypy passed for 131 source files.
- Backend non-integration suite: 858 passed, 107 deselected.
- Web lint and typecheck passed.
- Web suite: 139 passed.
- Web production build passed.

Two pre-existing untracked architecture documents under `docs/ops/` were deliberately excluded
from these commits.

## Current Release Stage

- M0-M4 and M8 are complete.
- M5 has converged three consecutive full 40-case real-provider synthetic-suite passes, but still
  needs representative-corpus review, provider usage/cost evidence, representative capacity, and
  managed alert/retention ownership.
- M6 has immutable staging, supply-chain, migration, rollback, database/R2 recovery, and 4C8G host
  evidence. The real 2C2G tiny-profile verification remains open. The independent fault-domain
  recovery drill remains the only approved externally blocked hard gate.
- M7 routing, fallback, breaker, and real-provider synthetic quality work are implemented. Provider
  revision/usage/cost/fallback telemetry and representative-corpus evidence remain open.
- GPU, vLLM, and quantization capacity work (`M7-R7`) is explicitly excluded from this release.

