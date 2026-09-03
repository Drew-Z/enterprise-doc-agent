# Document ACL/ABAC Design

## Authorization Boundary

`packages/core` owns the decision. `Document.access_mode` is `tenant` or
`restricted`; existing rows and new uploads default to `tenant`. A request is
visible only when the actor has an active membership in the active tenant and
the document is tenant-visible, owned by the actor, owned by a tenant owner, or
has a matching user/role grant. The predicate is expressed as SQL `EXISTS`
clauses and is applied before inventory, keyword recall, vector recall, and
Agent evidence are materialized.

Management is intentionally narrower than read access: the document creator or
tenant owner may change the mode and manage grants. Grant targets are either an
active tenant user or one of the existing tenant roles. Database constraints
prevent cross-tenant targets, invalid roles, and duplicate grants.

## Runtime Paths

- API inventory and ready-version endpoints pass tenant, actor, and role.
- Agent run creation checks the selected version through the same predicate.
- Agent status/events and artifact preview/download join the source document and
  re-evaluate visibility at read time.
- Signed Worker execution contexts carry actor identity. Worker-side policy
  reloads membership and document visibility from PostgreSQL, so changing a
  grant revokes subsequent tool calls without trusting an old role snapshot.
- Tool retrieval accepts actor identity and filters in SQL; legacy test doubles
  may omit the optional argument without changing production behavior.

## API Contract

`GET/PUT /api/documents/{document_id}/access` exposes the mode and an advisory
`canManage` flag. `GET/POST/DELETE /api/documents/{document_id}/grants` provides
grant administration. Reads use non-disclosing 404s; unauthorized mutations
use stable 403s. Policy and grant mutations append an audit event in the same
transaction with tenant, actor, resource, action, and policy summary.

## Migration and Rollback

Revision `20260825_0016_document_acl` adds the defaulted mode, validation/index,
and grant table. It is additive and can be downgraded after grants are removed;
no existing document data is rewritten. Deploy migration before application
rollout, then use the existing expand/migrate/contract rollback procedure.

## Deliberate Scope

This slice is an auditable document ACL with role grants and extension points for
future attributes. It does not claim SSO, arbitrary ABAC expressions, external
policy decision points, retention/legal hold, GPU capacity, or disaster recovery.
