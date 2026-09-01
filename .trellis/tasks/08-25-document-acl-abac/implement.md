# Document ACL/ABAC Implementation

## Completed Slices

- [x] Add `DocumentAccessMode` and `DocumentGrant` ORM models.
- [x] Add additive Alembic migration `20260825_0016_document_acl` with tenant,
  document, membership, role, uniqueness, and mode constraints.
- [x] Add `document_visible_to_actor` SQL predicate and
  `DocumentPolicyService` for idempotent policy/grant mutations.
- [x] Apply visibility to document inventory, ready Agent versions, hybrid
  keyword/vector recall, Agent policy reload, run status/events, and artifacts.
- [x] Add access and grant management endpoints with audit metadata and stable
  404/403 contracts.
- [x] Preserve tenant-wide compatibility for existing documents and legacy test
  doubles by keeping actor filters optional at core service boundaries.
- [x] Run targeted Ruff and mypy checks and the affected API/core tests.
- [x] Add PostgreSQL integration coverage for restricted visibility, creator/owner access,
  user and role grants, idempotent grant creation, live revocation, run/event/artifact
  non-disclosure, cross-tenant grant rejection, and audit writes.
- [x] Add management API contract tests for authenticated scope and stable
  403/404/422 responses.
- [x] Verify Alembic `20260825_0016` downgrade/upgrade round trip.
- [x] Add a responsive Documents access-policy drawer with access-mode updates,
  user/role grant creation, grant removal, bilingual labels, and runtime schema validation.
- [x] Add a real two-browser authorization flow backed by PostgreSQL and MinIO: an owner
  restricts an uploaded document, grants a member, and revokes that grant while the
  member session proves visibility changes after refresh.

## Verification Evidence

`uv run ruff check` passes for all changed Python modules and tests. Targeted
production-source `mypy` passes for 20 source files. The expanded Core, API,
Worker, Agent, MCP, and security regression passes with `652 passed`. The web
unit/component suite passes with `25 files, 172 passed`; ESLint, TypeScript, and
the Vite production build pass. Playwright includes desktop/mobile ACL and
showcase coverage plus a real owner/member grant-revocation scenario backed by
the local API, PostgreSQL, and MinIO.

## Follow-up

Keep arbitrary ABAC/PDP, SSO, retention execution/archival, GPU/model capacity,
and production disaster recovery as separate readiness gates.
