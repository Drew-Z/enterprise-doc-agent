# M0 Project Foundation: TDD Implementation Plan

## Preconditions

- Parent task `07-17-enterprise-document-agent-platform` remains in `planning` and owns cross-milestone acceptance.
- M0 remains in `planning` until the user explicitly approves this PRD, design, and implementation plan.
- Do not run `task.py start`, create `feat/m0-project-foundation`, or write product code before approval.
- The repository has no baseline commit yet. Before implementation, create an intentional reviewed baseline commit on `main`; do not let Trellis auto-commit unreviewed planning files.
- M1-M7 behavior is excluded from every M0 slice.

## TDD Slice Rules

For every slice:

1. Add one focused test or executable contract that fails for the intended missing behavior.
2. Record that the failure is expected and fails for the right reason.
3. Add only the minimum implementation needed to pass.
4. Run the focused test, then the affected package checks.
5. Refactor only after green and rerun the complete `Validate` block a second time.
6. Stop at the listed rollback point if the slice cannot be made green without crossing M0 scope.

Each slice records two successful validation runs: one for the minimal green implementation and one after refactor. A slice is not complete if only the pre-refactor run exists.

## Activation Procedure

This is workflow activation, not a product behavior slice, so it is not represented as a synthetic TDD red/green cycle. Run it only after the user approves the M0 plan.

1. Confirm the repository is still on the unborn `main` branch and inspect every untracked file.
2. Confirm `.trellis/config.yaml` contains `session_auto_commit: false` and `codex.dispatch_mode: inline`, with package mappings still absent.
3. Create the reviewed Trellis/planning baseline commit on `main`.
4. Create `feat/m0-project-foundation` from that exact commit.
5. Start the M0 Trellis task, which changes its status from `planning` to `in_progress`.

```powershell
if ((git branch --show-current) -ne "main") { throw "Expected main before baseline commit" }
git status --short
Select-String -Path .trellis/config.yaml -Pattern '^session_auto_commit: false$'
Select-String -Path .trellis/config.yaml -Pattern '^  dispatch_mode: inline$'
python ./.trellis/scripts/task.py validate 07-17-m0-project-foundation
git add .agents .codex .trellis AGENTS.md
git commit -m "chore: initialize trellis workflow"
git switch -c feat/m0-project-foundation
python ./.trellis/scripts/task.py start 07-17-m0-project-foundation
if ((git branch --show-current) -ne "feat/m0-project-foundation") { throw "M0 branch not active" }
git log -1 --oneline
python ./.trellis/scripts/task.py list
```

**Rollback point**: Before `task.py start`, switch back to `main` and delete only the unstarted feature branch. After `task.py start`, restore task metadata deliberately; never reset or discard unrelated user changes.

## Execution Order

### Slice 1: Repository and locked toolchain contract

**Observable behavior**: A clean checkout has the required monorepo paths, importable Python packages, a pnpm Web workspace, and reproducible dependency installation.

- **Red**: Add `tests/foundation/test_repository_contract.py` to assert required paths, workspace members, package names, Python range, Node engine, and root quality commands. Confirm it fails against the empty repository.
- **Green**: Add root uv/pnpm manifests, `src`-layout package manifests, minimal import modules, `.gitignore`, `.env.example`, tool-version files, and lockfiles.
- **Refactor**: Centralize only configuration genuinely shared by all Python packages; keep application dependencies app-owned.
- **Validate**:

  ```powershell
  uv sync --frozen
  pnpm install --frozen-lockfile
  uv run pytest tests/foundation/test_repository_contract.py
  uv run python -c "import enterprise_doc_core, enterprise_doc_api, enterprise_doc_worker"
  ```

- **Rollback point**: Revert only workspace manifests and empty package skeletons; no migration or runtime state exists yet.

### Slice 2: Typed configuration and secret handling

**Observable behavior**: API and Worker settings parse valid environment values, reject invalid/non-local defaults, and redact credentials.

- **Red**: Add tests for nested environment parsing, required values, known local credentials outside local mode, `SecretStr` representation, and safe settings serialization.
- **Green**: Implement Core settings groups plus API/Worker composition and explicit settings factories.
- **Refactor**: Deduplicate only shared infrastructure fields; keep host/port/service defaults in their owning application.
- **Validate**:

  ```powershell
  uv run pytest packages/core/tests/test_settings.py apps/api/tests/test_settings.py apps/worker/tests/test_settings.py
  uv run mypy packages/core/src apps/api/src apps/worker/src
  ```

- **Rollback point**: Return to import-only packages and preserve the lockfiles from Slice 1.

### Slice 3: Dependency checks and API health contracts

**Observable behavior**: API liveness is dependency-free; readiness is concurrent, bounded, typed, and returns 503 on required dependency failure.

- **Red**: Add ASGI tests for live 200, ready 200, failed/timeout ready 503, response schema, and proof that liveness never invokes dependency checkers.
- **Green**: Implement dependency-check protocols/aggregate logic in Core, the API app factory, and live/ready routes with injected fakes for tests.
- **Refactor**: Separate health policy from concrete PostgreSQL/Redis/MinIO adapters; keep HTTP status mapping in API.
- **Validate**:

  ```powershell
  uv run pytest packages/core/tests/test_health.py apps/api/tests/test_health.py
  uv run ruff check packages/core apps/api
  uv run mypy packages/core/src apps/api/src
  ```

- **Rollback point**: Remove API routes and concrete checker adapters while preserving the typed configuration contract.

### Slice 4: Request context and structured logging

**Observable behavior**: Concurrent API requests receive isolated request/correlation IDs in response headers, logs, and spans without leaking secrets.

- **Red**: Add tests for ID generation, valid propagation, invalid replacement, context cleanup across concurrent requests, stable JSON log keys, and credential/body redaction.
- **Green**: Implement context-variable middleware, response headers, JSON logging configuration, request completion events, and safe exception logging.
- **Refactor**: Move shared logging/context helpers to Core while keeping ASGI middleware API-owned.
- **Validate**:

  ```powershell
  uv run pytest packages/core/tests/test_logging.py apps/api/tests/test_request_context.py
  uv run ruff format --check packages/core apps/api
  uv run ruff check packages/core apps/api
  ```

- **Rollback point**: Restore the health-only API; do not retain partial middleware that can leak context between requests.

### Slice 5: Worker process lifecycle and probes

**Observable behavior**: Worker starts independently, exposes live/ready probes, reports dependency failure, and shuts down cleanly after a termination signal.

- **Red**: Add tests for Worker startup, live 200, ready 200/503 through fake checkers, lifecycle log/span emission, and bounded graceful shutdown.
- **Green**: Implement the Worker application factory, internal probe server, dependency-check integration, signal handling, and empty run loop.
- **Refactor**: Share checker and observability factories through Core; keep process supervision and ports Worker-owned.
- **Validate**:

  ```powershell
  uv run pytest apps/worker/tests/test_lifecycle.py apps/worker/tests/test_health.py
  uv run ruff check apps/worker packages/core
  uv run mypy apps/worker/src packages/core/src
  ```

- **Rollback point**: Remove the Worker entry point and probe adapter; retain Core contracts already proven by API tests.

### Slice 6: OpenTelemetry bootstrap

**Observable behavior**: Disabled telemetry is a no-op, enabled telemetry emits API request and Worker lifecycle spans, and initialization/shutdown are idempotent.

- **Red**: Add in-memory exporter tests for disabled mode, emitted route-template spans, Worker lifecycle spans, safe attributes, repeated initialization, and shutdown.
- **Green**: Implement shared OTel bootstrap, API instrumentation, Worker manual spans, and optional OTLP exporter configuration.
- **Refactor**: Keep exporter construction behind a factory so unit tests and future M5 collector deployment do not alter application code.
- **Validate**:

  ```powershell
  uv run pytest packages/core/tests/test_telemetry.py apps/api/tests/test_telemetry.py apps/worker/tests/test_telemetry.py
  uv run mypy packages/core/src apps/api/src apps/worker/src
  ```

- **Rollback point**: Disable instrumentation at application factories and retain logging/health behavior; startup must remain green without telemetry.

### Slice 7: Compose infrastructure and initial migration

**Observable behavior**: Compose reaches healthy state, buckets initialize idempotently, and Alembic upgrade/downgrade/re-upgrade works against an empty database.

- **Red**: Add `tests/foundation/test_migration_contract.py` and infrastructure smoke tests that fail because Compose, concrete checkers, Alembic configuration, and the initial revision do not exist. The migration test must assert that only the required `vector` extension is introduced by M0.
- **Green**: Add PostgreSQL/pgvector, Redis, MinIO, bucket initialization, named volumes, health checks, concrete async dependency adapters, SQLAlchemy engine factory, Alembic configuration, the `vector`-only initial migration, and `scripts/foundation_smoke.py --preflight`.
- **Refactor**: Deduplicate environment names across `.env.example`, Compose, and typed settings without moving secrets into tracked files.
- **Validate**:

  ```powershell
  docker version
  docker compose version
  uv run python scripts/foundation_smoke.py --preflight
  docker compose -f infra/compose/docker-compose.yml config
  docker compose -f infra/compose/docker-compose.yml up -d --wait
  uv run alembic upgrade head
  uv run pytest tests/foundation/test_migration_contract.py -m integration
  uv run alembic downgrade base
  uv run alembic upgrade head
  uv run pytest tests/foundation -m integration
  docker compose -f infra/compose/docker-compose.yml down
  ```

- **Environment preconditions**: Docker Engine/Desktop is running; Compose v2 supports `--wait`; configured PostgreSQL/Redis/MinIO/Web/API/Worker ports are free or explicitly overridden; a local `.env` was created from `.env.example`; the target database is the disposable M0 local database; and local volume deletion requires explicit confirmation.
- **Rollback point**: Stop Compose and remove only M0-created local volumes after explicit confirmation; never rewrite an applied migration in a shared environment.

### Slice 8: React operational dashboard shell

**Observable behavior**: The first screen shows real platform readiness with stable loading, healthy, degraded, unreachable, and refresh interactions on desktop and mobile.

- **Red**: Add Vitest/Testing Library tests for typed health parsing, all display states, retry, accessible status semantics, and layout stability.
- **Green**: Implement the Vite/React application, TanStack Query provider, typed health client, compact operational overview, responsive styles, and icon-based refresh control with tooltip.
- **Refactor**: Keep server state in TanStack Query and UI-only state local; remove mock data and unused navigation.
- **Validate**:

  ```powershell
  pnpm --filter web lint
  pnpm --filter web typecheck
  pnpm --filter web test -- --run
  pnpm --filter web build
  ```

- **Manual visual gate**: Start API and Web; verify with Playwright screenshots at desktop and mobile widths that text does not overlap, the status layout is stable, and loading/error content does not shift fixed controls.
- **Rollback point**: Return to the minimal Vite entry point while preserving the tested health client contract.

### Slice 9: Root quality commands and baseline GitHub Actions

**Observable behavior**: Local root commands and pull-request CI run the same locked backend/frontend quality checks and fail on any failing check.

- **Red**: Add a repository contract test that inspects required workflow jobs/commands and rejects `continue-on-error` or equivalent allow-failure paths.
- **Green**: Add root scripts and the baseline GitHub Actions workflow for backend and frontend checks, lockfile caching, and test reports where available.
- **Refactor**: Reuse root commands in CI; avoid duplicating long command sequences in workflow YAML.
- **Validate**:

  ```powershell
  uv run ruff format --check .
  uv run ruff check .
  uv run mypy packages/core/src apps/api/src apps/worker/src
  uv run pytest -m "not integration"
  pnpm lint
  pnpm typecheck
  pnpm test
  uv run pytest tests/foundation/test_ci_contract.py
  ```

- **Rollback point**: Keep local checks runnable even if the workflow file must be removed or corrected.

### Slice 10: Clean-environment smoke and specification capture

**Observable behavior**: A new environment can follow one documented sequence to start dependencies, migrate, run API/Worker/Web, observe healthy probes, and run all M0 gates; Trellis specs describe only implemented patterns.

- **Red**: Add `tests/foundation/test_documentation_contract.py` to require the README's exact install/start/migrate/check commands, the real `scripts/foundation_smoke.py` entry point, and non-placeholder Trellis package mappings/spec references. Add `tests/foundation/test_evidence_contract.py` to require the parent evidence schema, immutable `evidence/m0/` manifest path, indexed manifest, exact command results, commit SHA, artifacts, limitations, and owner. Confirm both tests fail before documentation/spec/evidence finalization.
- **Green**: Finalize README commands and extend `scripts/foundation_smoke.py` with a `--run` path that performs preflight, starts Compose, migrates, launches API/Worker/Web, polls their probes, runs the Web availability check, terminates child processes, and reports a non-zero exit on any failure. Add Trellis monorepo package mappings, update project-specific backend/frontend/guides specs from real code, archive `00-bootstrap-guidelines` after review, write `evidence/m0/<YYYYMMDD-HHMMSS>-m0-project-foundation.json`, store referenced logs/screenshots, and update `evidence/index.json`.
- **Refactor**: Remove duplicated setup prose and keep a single source for ports, environment names, and quality commands.
- **Validate**:

  ```powershell
  uv run pytest tests/foundation/test_documentation_contract.py
  uv run pytest tests/foundation/test_evidence_contract.py
  python ./.trellis/scripts/get_context.py --mode packages
  python ./.trellis/scripts/task.py validate 07-17-m0-project-foundation
  uv run python scripts/foundation_smoke.py --preflight
  uv run python scripts/foundation_smoke.py --run
  uv run ruff format --check .
  uv run ruff check .
  uv run mypy packages/core/src apps/api/src apps/worker/src
  uv run pytest
  pnpm lint
  pnpm typecheck
  pnpm test
  pnpm --filter web build
  git status --short
  ```

- **Rollback point**: Restore the last reviewed specs and configuration; do not archive M0 unless the smoke path remains reproducible.

## M0 Review Gates

### Gate A: Scope

- No Multipart, durable job runtime, RAG, LangGraph, MCP, production deployment, or measured-capacity claim has entered the codebase.

### Gate B: Reproducibility

- Locked installs, Compose health, migration, and root checks pass from a clean environment.

### Gate C: Runtime contracts

- API and Worker live/ready semantics, graceful shutdown, request context, secret-safe logs, and optional tracing pass focused tests and the smoke path.

### Gate D: Frontend

- Dashboard health states pass unit tests and visual inspection at mobile and desktop widths without fake operational data.

### Gate E: Trellis and Git evidence

- Actual conventions are captured in specs.
- Bootstrap is archived only after the specs are factual.
- M0 code and evidence are committed intentionally on `feat/m0-project-foundation`.
- The indexed M0 evidence manifest names the reviewed commit, exact command outcomes, artifacts, limitations, and owner, and passes `test_evidence_contract.py`.
- M0 is not archived until the user reviews the completed evidence.

## Final Validation Matrix

```text
repository contract
locked uv and pnpm install
backend format/lint/typecheck/unit
frontend lint/typecheck/unit/build
Compose config and health
Alembic upgrade/downgrade/re-upgrade
API live/ready success and failure
Worker live/ready and graceful shutdown
request/correlation structured-log contract
in-memory OTel contract
desktop/mobile dashboard visual check
clean foundation smoke
machine-readable M0 evidence manifest and parent index
Trellis validation and Git status review
```

## M0 Completion and Rollback Rules

- Never mark a target or documented command as passing without captured command output from the current repository state.
- Never hide a failing check with skip, xfail, allow-failure, or repeated reruns.
- Do not mutate the initial migration after another milestone depends on it.
- Do not delete shared database or object-store volumes as part of an automatic rollback.
- If M0 cannot remain runnable on its branch, return to the last green slice and record the failure in task evidence before proceeding.
- After M0 is accepted and archived, plan M1 and M2 from the actual package/configuration contracts rather than this plan's assumptions.
