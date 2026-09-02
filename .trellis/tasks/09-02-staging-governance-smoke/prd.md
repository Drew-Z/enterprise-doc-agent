# Staging Governance Smoke

## Goal

Provide a repeatable, manually dispatched staging check for the governance
surfaces that are not covered by the main upload/Agent smoke. The check must
prove the deployed API enforces owner/member document access, owner-only audit
governance, and external identity-binding lifecycle behavior using a dedicated
synthetic staging tenant.

## Requirements

- Accept the staging API base URL and exact control-plane/object-store host
  allowlists as normal workflow inputs or environment variables.
- Read owner and member bearer tokens only from process environment variables;
  never accept them as CLI arguments, print them, or persist them.
- Exercise a bounded synthetic document flow: owner upload, set restricted,
  grant member access, verify member visibility, revoke the grant, and verify
  member access is removed.
- Exercise owner-only audit governance: update a short-lived retention policy,
  create and release a legal hold, run retention preview/plan, archive one
  eligible batch when available, and verify the latest batch when available.
- Exercise identity binding lifecycle: discover an active member, create a
  synthetic issuer/subject binding, deactivate it, reactivate it, and verify the
  binding list after each mutation.
- Treat pre-existing eligible archive batches as optional: the smoke must still
  validate retention policy, legal hold, preview and plan when there is nothing
  eligible to archive, without deleting existing audit events.
- Emit a sanitized JSON report containing schema version, scenario, step names,
  pass/fail status, bounded counts and HTTP status classes only. Redact all
  identifiers, URLs with query strings, object keys, document content and
  credentials.
- Fail closed on non-HTTPS or unallowlisted endpoints and on unexpected API
  responses. Keep each request and the whole run within explicit timeouts.
- Add a manual `workflow_dispatch` governance gate that is opt-in and uses the
  protected staging environment. It must not widen Kubernetes RBAC or read
  Kubernetes Secrets.

## Acceptance Criteria

- [x] `scripts/staging_governance_smoke.py` implements the three behavior
      groups above and reuses the reviewed HTTPS/redirect-safe client boundary.
- [x] Focused tests cover happy path, owner/member authorization failures,
      optional archive behavior, endpoint allowlists and report redaction.
- [x] `deploy-staging.yml` exposes a separate opt-in governance smoke step with
      environment-protected owner/member tokens and sanitized evidence upload;
      the existing main smoke remains unchanged.
- [x] Workflow and report contracts reject token-bearing command lines and
      signed URLs in artifacts.
- [x] Ruff, mypy, focused tests and deployment contract tests pass. `actionlint`
      is not installed on this host and remains a remote/manual check.
- [x] The workflow can only produce a governance `passed` result when all
      required steps succeed; otherwise the release remains blocked and no
      formal tag is created.

## Notes

- This is staging governance evidence, not proof of external IdP/SSO, complete
  ABAC/PDP, WORM compliance, production capacity or disaster recovery.
- The synthetic tenant and principals are provisioned out of band. The workflow
  receives short-lived tokens for that tenant through GitHub Environment
  secrets; token issuance and rotation remain operator-owned.
