# Design: Public-reference-inspired RAG evaluation suite

## Boundaries

- enterprise_doc_core.evaluation.rag_quality remains the sole schema, loader, hashing, anchor
  validation, and scoring implementation. This task consumes it unchanged.
- evaluation/rag_quality_public_reference_v1.json is a sibling of the immutable v2 dataset; it is
  not an amendment or replacement.
- evaluation/corpus/public_reference_inspired_v1 is a new corpus root containing only four
  fictional Northstar Ledger documents.
- A provenance Markdown file lives beside the dataset. Because the schema forbids extra fields,
  provenance is not introduced through speculative JSON schema changes.
- Repository contract tests stay beside existing loader tests in
  packages/core/tests/test_rag_quality_evaluation.py and use real files.

## Dataset and corpus contract

The dataset uses schema version 1, version enterprise-rag-quality-public-reference-v1, and corpus
root corpus/public_reference_inspired_v1.

| Document key | File | Anchor IDs |
| --- | --- | --- |
| resilience-standard | service-resilience-standard.txt | res.slo, res.sla-boundary, res.retry-evidence, res.exclusion |
| incident-handling-runbook | incident-handling-runbook.txt | ir.event, ir.incident, ir.containment, ir.evidence, ir.recovery |
| access-governance-standard | access-governance-standard.txt | access.requester, access.owner, access.revocation, access.audit |
| untrusted-content-standard | external-content-safety-standard.txt | unsafe.external, unsafe.no-secret, unsafe.approval, unsafe.encoded |

The fixed case IDs are:

- Facts: pub-fact-slo, pub-fact-sla-boundary, pub-fact-event, pub-fact-incident,
  pub-fact-access-duration, pub-fact-audit-fields.
- Hard negatives: pub-hard-sla-vs-slo, pub-hard-retry-evidence,
  pub-hard-event-vs-incident, pub-hard-containment-order, pub-hard-self-approval,
  pub-hard-revocation.
- Refusals: pub-refuse-supplier-credit, pub-refuse-breach-law,
  pub-refuse-employee-phone.
- Citations: pub-citation-evidence, pub-citation-remediation.
- Safety: pub-safety-imported-delete, pub-safety-secret-exfiltration,
  pub-safety-encoded-injection.

All answer cases use existing answer/citation semantics. Refusals carry no facts or anchors and
accept empty_evidence, insufficient_evidence, and low_relevance. Safety cases are grounded
answers because the synthetic documents contain the controlling safety rule; they are not
runtime tool-execution requests.

## Data flow

1. Original corpus text, anchors, and labels are edited as one review unit.
2. load_rag_quality_dataset resolves the independent corpus root, validates quotes and references,
   and computes hashes.
3. Focused tests inspect loaded models instead of duplicating loader logic.
4. The runner in validate-only mode writes a local report and exits before any staging or provider
   client.
5. A separate future task may propose provider execution only after independent label/content
   review and explicit external-gate approval.

## Compatibility

- No database migration, API, dependency, service configuration, model setting, or deployment.
- Existing v1/v2 files remain in place. Tests pin v2's pre-task dataset and corpus SHA-256 values.
- Successful static validation makes no statement about route identity, model quality, cost,
  capacity, or runtime availability.

## Source and licensing posture

Scenario inspiration is limited to NIST CSF 2.0, NIST SP 800-61 Rev. 3, Microsoft Azure
documentation about interpreting SLAs, UConn's public incident response plan, and OWASP
LLM01:2025 Prompt Injection. The corpus copies none of their sentences, values, contacts, or
organization-specific terms and claims no certification or adoption.

An authoritative OWASP licensing page was not confirmed in this research pass. OWASP therefore
remains conceptual attribution only; no OWASP text may be copied. Independent human content and
semantic review is mandatory before any provider experiment.

## Trade-offs

- Four short documents and 20 cases are reviewable on current hardware, but are not representative
  customer data.
- Reusing the strict schema keeps scope narrow; provenance belongs in Markdown and dataset
  limitations rather than new JSON fields.
- Exact labels and anchors are deterministic regression controls, not semantic adjudication.

## Rollout and rollback

This is a repository-only data/test addition. Rollout means merge plus local validation and has
no deployment step. If review rejects it, revert the new dataset, corpus, provenance document,
and focused tests as one unit. Never rewrite existing datasets or sealed provider evidence.

