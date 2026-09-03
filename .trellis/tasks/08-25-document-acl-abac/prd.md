# PRD: Implement document-level authorization

## Goal

Add a server-authoritative document access boundary that can restrict a document
from other members of the same tenant, while preserving the current tenant-wide
behavior for existing documents. The same decision must protect document
inventory, retrieval, Agent run creation, and Agent artifact access.

## Confirmed Repository Facts

- `Document` and `DocumentVersion` currently carry `tenant_id` but no document
  policy or grant relation.
- `DocumentInventoryService.list_versions` filters only by `tenant_id`.
- `HybridRetrievalService` filters both recall paths only by tenant and version.
- `AgentService` validates the selected version only by tenant, readiness, and
  ingestion generation; artifact reads validate tenant membership and run tenant
  ownership.
- The authenticated principal has `tenant_id`, `actor_id`, and a fixed
  `owner`/`member` role. The API is already server-authoritative for tenant
  membership and audit export permissions.
- Existing showcase and API contracts assume tenant-wide document visibility.

## Product Decision

New and existing documents remain `tenant` visible unless explicitly switched
to `restricted`; restricted documents require grants. This avoids a breaking
migration and lets the project demonstrate a real document-level restriction
without invalidating existing demo data. The trade-off is that the first release
is opt-in restriction rather than a strict zero-trust default.

## Requirements

### R1. Policy model

- Add a document access mode with `tenant` and `restricted` values.
- Add explicit grants scoped to a document for a user and/or tenant role.
- The document creator and tenant owners retain management access.
- Reject invalid grant targets and duplicate grants at the database boundary.

### R2. Server enforcement

- Inventory queries return only documents visible to the authenticated actor.
- Retrieval keyword and vector recall apply the same visibility predicate before
  ranking, never filtering unauthorized candidates after model input is built.
- Agent run creation rejects an unauthorized document version with the same
  non-disclosing not-found/forbidden contract used for cross-tenant resources.
- Agent artifact preview/download and run event access do not leak a restricted
  source document through citations or metadata.
- UI capability hints remain advisory; API authorization remains final.

### R3. Management API

- Owners and document creators can read and update a document's access mode.
- Owners and document creators can list, add, and remove grants.
- Members without management access receive a stable 403 response for policy
  mutation and cannot infer grant membership for another restricted document.
- Every policy or grant mutation writes an audit event containing the tenant,
  actor, document resource, action, and resulting policy summary.

### R4. Compatibility and migration

- Existing rows migrate to `tenant` visibility with no data rewrite beyond the
  new policy columns/table.
- Existing upload and document APIs continue to work for tenant-visible files.
- The local showcase remains read-only and explicitly labels policy data as a
  fixture if displayed.

## Acceptance Criteria

- A member can list and retrieve a tenant-visible document exactly as today.
- A restricted document is absent from inventory and retrieval for an ungranted
  member, while an explicitly granted member can access it.
- Cross-tenant access remains denied even when a grant or version identifier is
  supplied directly.
- Agent run creation, artifact preview/download, and audit queries all enforce
  the same document decision.
- Owner/creator policy and grant mutations are idempotent, audited, and covered
  by API and core service tests.
- Migration upgrade/downgrade and type/lint checks pass.
- No acceptance criterion depends on GPU, real model capacity, SSO provider
  credentials, or production disaster-recovery infrastructure.

## Out of Scope

- OIDC/SAML SSO and external group provisioning.
- Arbitrary attribute expressions, row-level policy languages, or external PDP
  integration. The grant model should leave an extension point for ABAC but not
  implement a policy engine in this slice.
- Retention, legal hold, WORM storage, and production compliance certification.
- GPU/vLLM, model-quality, capacity, and multi-region recovery evidence.
