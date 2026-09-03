# Implementation Plan

1. [x] Add a reusable response/step model and redaction helpers in
   `scripts/staging_governance_smoke.py`.
2. [x] Implement the owner/member restricted-document flow with explicit status
   checks and bounded inventory assertions.
3. [x] Implement retention/legal-hold/preview/plan/archive verification and safe
   compensation for a created hold.
4. [x] Implement identity-binding create/deactivate/activate flow against the
   active-member list.
5. [x] Add focused fake-client tests for all behavior slices and redaction/allowlist
   failures before wiring CI.
6. [x] Add the opt-in workflow step and sanitized evidence capture; extend release
   outcome contracts without exposing secret values.
7. [x] Re-run focused tests, the non-integration regression, deployment contracts,
   Ruff and mypy after the final cleanup-path changes. Review the diff for secret
   and identifier leakage before commit; `actionlint` is unavailable locally, so
   the changed workflow remains subject to the remote GitHub Actions check.
