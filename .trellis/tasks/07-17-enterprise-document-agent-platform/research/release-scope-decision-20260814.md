# Release Scope Decision 2026-08-14

## Decision

The current release remains a CPU/OpenAI-compatible staging release. GPU, vLLM, and quantization
capacity work (`M7-R7`) is explicitly excluded from this release and must not be represented as a
passed benchmark or production-capacity claim.

All other M5/M6/M7 hard gates remain in scope. The existing server should be used to close every
gate it can support: real-provider quality, staging and CI/CD evidence, image supply chain,
secrets/network/service identity, migration compatibility, backup/restore, rollback, monitoring,
audit and incident procedures. A gate may remain open only when its required environment is truly
unavailable and the linked manual-gate record states the missing prerequisite.

The independent fault-domain migration/recovery drill (`M6-R5`) is the explicit external exception.
It requires another server or an independently isolated recovery target plus an independent reviewer;
the existing single-node recovery evidence remains retained but cannot close that production-like
gate.

## Consequences

- M7 routing and real-provider quality remain active work; only its GPU/vLLM capacity branch is out
  of scope.
- M5 managed observability and representative capacity remain included, but can be marked
  `blocked_external` only after the current server and available staging evidence are checked.
- M6 tiny-profile stabilization remains included until its actual staging/review status is known;
  it is not silently replaced by the 4C8G profile.
- Parent Gate E can rely on this explicit scope decision for the optional M7 GPU branch, while the
  parent production-readiness gate remains blocked by any unresolved external prerequisites.

## Evidence

- Structured decision: `evidence/gates/release-scope-decision-20260814.json`.
- Existing M6 recovery record: `evidence/gates/m6-backup-restore-rollback.json`.
- Existing GPU manual gate: `evidence/gates/m7-gpu-vllm-capacity.json`.
