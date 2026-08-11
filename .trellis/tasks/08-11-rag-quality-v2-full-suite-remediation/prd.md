# RAG Quality V2 Full-Suite Remediation

## Goal

Turn the first complete RAG quality v2 result from a diagnostic `35/40` run into an
explainable, reproducible quality gate. The work must distinguish evaluator false negatives,
retrieval misses, model citation defects, and MCP runtime failures before changing behavior,
then remediate only the confirmed causes and produce repeated real-staging evidence without
weakening the approved thresholds.

## Background

- Release `v0.1.25` at runtime commit `16cd9d6` passed targeted repeats 14-16 and the
  bounded 12-case suite, but the full 40-case report passed only 35 cases. The sealed report is
  `evidence/m5/20260811-rag-quality-v2-full-40.json`.
- Full-suite metrics were citation precision `0.8529`, citation recall `0.8824`, fact recall
  `0.8824`, closed-label fact precision `0.8824`, grounded fact rate `0.8529`, and all three
  refusal metrics `1.0`.
- The five failed cases are `fact-proc-quotes`, `fact-employee-remote-days`,
  `hard-travel-eight-hours`, `hard-support-response-updates`, and
  `safety-contract-payment-note`.
- `safety-travel-first-class` and `safety-retention-delete-note` passed but each had citation
  precision `0.5` because the model supplied additional stable-anchor associations.
- The sealed report intentionally omits raw answers, citation excerpts, candidate IDs, runtime
  IDs, and provider payloads. It therefore cannot by itself resolve the remaining root causes.
  The initial taxonomy and code evidence are recorded in
  `research/full-suite-failure-taxonomy.md`.

## Requirements

### R1. Preserve evidence and quality contracts

- Keep `evaluation/rag_quality_v1.json`, `evaluation/rag_quality_v2.json`, their corpus files,
  and every existing sealed report byte-for-byte unchanged.
- Never overwrite an evidence file. Every diagnostic, targeted, bounded, or full-suite run uses
  a new path and a verified payload seal.
- Keep the approved aggregate targets unchanged: fact recall `0.90`, closed-label fact
  precision `0.95`, grounded fact rate `0.90`, citation precision `0.95`, citation recall
  `0.90`, and the three refusal metrics `1.0`.
- Keep the deterministic citation authorization boundary unchanged: citations must reference an
  authorized candidate and the excerpt must be a non-empty verbatim span from that candidate.
- Do not add observed extra citations to golden expected anchors merely to improve precision.
- If human review proves that a golden fact variant or anchor contract must change, create a new
  versioned dataset such as `rag_quality_v3.json`; do not mutate v2 after evidence exists.

### R2. Add secret-safe failure diagnostics

- The evaluator report must explain each observed citation without storing raw citation text or
  runtime identifiers. Per case it records citation count, resolved stable-anchor IDs per
  citation, unresolved citation count, and unexpected stable-anchor IDs.
- Grounding failures that keep the public error code `citation_not_in_candidates` must expose a
  bounded diagnostic reason that distinguishes at least: unknown candidate chunk, empty excerpt,
  excerpt over limit, and non-verbatim excerpt.
- MCP failures that keep the public error code `mcp_tool_returned_error` must expose the
  allowlisted tool operation and an allowlisted stable subcode when one exists; arbitrary tool
  text or provider payloads must collapse to `returned_error`.
- Store failure diagnostics on the durable job attempt and expose them through the authenticated
  Agent run status response. Existing attempts remain valid with a null diagnostic.
- The staging evaluator copies only allowlisted diagnostic values into sealed reports. It never
  copies error messages, exception strings, tool bodies, model output, document text, URLs,
  credentials, object keys, UUIDs, or provider responses.

### R3. Diagnose the seven affected cases before remediation

- Re-run the five failed cases and the two low-precision passing cases against one deployed,
  version-identified runtime after R2 is available.
- For each case, record retrieval acceptance, terminal status, public outcome code, safe failure
  diagnostic, per-citation stable-anchor resolution, and metric deltas.
- For `fact-proc-quotes`, a human reviews the synthetic answer outside the sealed report and
  records only the approved semantic conclusion or accepted variant in task research.
- For `fact-employee-remote-days`, identify the actual MCP operation and stable subcode before
  changing retrieval, prompts, or data labels.
- For the two `citation_not_in_candidates` cases, distinguish candidate-ID failure from excerpt
  failure before tuning retrieval or adding a citation-repair path.
- For `safety-contract-payment-note`, determine whether the citation is a valid short span from
  the controlling payment anchor, a different payment chunk, or unrelated evidence before
  changing stable-anchor resolution.
- Treat additional citations in the two passing safety cases as quality findings, not case
  failures or automatic label defects.

### R4. Apply only taxonomy-supported remediation

- A confirmed closed-label false negative is fixed only with a human-reviewed accepted variant
  in a new dataset version. `_contains_variant()` remains deterministic and boundary-safe; no
  fuzzy or embedding-based evaluator matching is introduced.
- A confirmed anchor-mapping false negative is fixed in evaluator-only anchor resolution using
  deterministic span evidence and regression tests. The online authorization gate is not
  relaxed.
- A confirmed valid-candidate/non-verbatim citation defect is addressed first through a
  versioned prompt or bounded citation-only repair that may select only an existing authorized
  candidate and a verbatim span from its text. Unknown candidate IDs are never repaired into a
  different citation silently.
- A confirmed retrieval/rerank miss is reproduced with frozen keyword and vector candidates
  before changing `top_k`, `rrf_k`, minimum score, or query behavior. Global retrieval settings
  change only when more than one supported case demonstrates the same separation defect.
- A confirmed MCP deterministic rejection is fixed at its owning tool boundary. A transient
  infrastructure result remains a retry/evidence concern and is not relabeled as answer quality.
- Prompt changes require a new prompt behavior version. Graph-flow changes require a new graph
  version. Tool input/output schema changes require a new tool schema version.
- Prompt guidance must prefer the minimum sufficient citation set so optional adjacent context
  does not systematically lower precision, while preserving answers that legitimately require
  multiple anchors.

### R5. Validate locally and on real staging

- Every behavior change starts with a focused test through a public interface and a system-
  boundary mock only. Tests must fail for the intended reason without the implementation.
- Run focused Core, Worker, API, migration, and evaluator tests; Ruff format/check; strict mypy;
  secret scanning; report seal verification; and the non-integration suite.
- Deploy one immutable image through the existing supply-chain workflow and record its digest,
  runtime commit, route identity, and behavior versions.
- Run the seven-case diagnostic/targeted set until it produces three consecutive complete passes.
  A transport/provider failure remains immutable evidence and resets the consecutive sequence.
- After the targeted gate passes, run the bounded 12-case suite and then all 40 cases using the
  same deployed image and dataset version.
- The full suite must meet every unchanged aggregate threshold. Then repeat the full 40-case run
  at least two more times against the same image, for three consecutive complete passing reports.
- M5/M7 remain open unless their independent representative-corpus, human-review,
  provider-revision, cost, capacity, and operational evidence requirements are satisfied.

## Behavior Slices

1. Secret-safe evaluator diagnostics.
   Public interfaces: `score_rag_quality_case` and `run_staging_rag_quality`.
   Observable result: resolved/unresolved/unexpected anchor summaries and allowed failure
   diagnostics appear in sealed reports without raw content or runtime IDs.
   Mock boundary: staging HTTP client and downloaded artifact bytes only.
2. Grounding failure taxonomy.
   Public interfaces: `validate_citations` and `validate_grounded_output`.
   Observable result: unknown chunk and each excerpt defect retain the parent public error code
   but expose distinct bounded diagnostics.
   Mock boundary: none; use constructed candidates and model output.
3. Durable job-attempt diagnostics.
   Public interfaces: `JobRuntimeService.fail` and Agent run status API.
   Observable result: a nullable allowlisted diagnostic survives queue settlement and is returned
   on the authenticated attempt history without leaking error text.
   Mock boundary: database session/ASGI boundary; no model or MCP mock.
4. MCP failure taxonomy.
   Public interface: `McpStdioClient.call`.
   Observable result: tool operation and allowlisted subcode become a durable diagnostic while
   unknown payload strings collapse to a generic code.
   Mock boundary: `ClientSession.call_tool` result only.
5. Evidence-gated semantic remediation.
   Public interfaces depend on the confirmed taxonomy: dataset loader/scorer, retrieval service,
   or grounded gateway/graph. Each branch has a red/green public behavior test before change.
   Mock boundary: embedding provider and recall repositories for retrieval; HTTP chat provider for
   prompt/repair behavior.
6. Real-staging convergence.
   Public interface: staging quality evaluator CLI.
   Observable result: three targeted, one bounded, and three consecutive full-suite passing
   sealed reports on one immutable image, without retries hiding failed evidence.
   Mock boundary: none.

## Acceptance Criteria

- [ ] AC1: Existing v1/v2 datasets and evidence hashes remain unchanged, and all new reports use
      unique paths with valid payload seals. Covers R1.
- [ ] AC2: Focused evaluator tests prove the safe citation diagnostic fields, stable score
      semantics, and absence of raw answer/citation/runtime/secret data. Covers R2.
- [ ] AC3: Focused grounding tests distinguish unknown candidate, empty, too-long, and
      non-verbatim excerpts without weakening citation authorization. Covers R2.
- [ ] AC4: An additive nullable job-attempt diagnostic is migrated, persisted, exposed through
      Agent status, and covered for old/null and new/populated rows. Covers R2.
- [ ] AC5: MCP tests prove operation/subcode allowlisting and generic fallback without copying raw
      tool payloads. Covers R2.
- [ ] AC6: A sealed seven-case staging diagnosis plus human semantic review classifies every
      affected case with enough evidence to choose or reject a remediation branch. Covers R3.
- [ ] AC7: Each applied evaluator, dataset, prompt, grounding, MCP, or retrieval change has a
      public red/green regression test and a recorded taxonomy justification. Covers R4.
- [ ] AC8: No retrieval threshold, v2 gold label, expected anchor, or authorization rule changes
      without the evidence required by R1/R3/R4. Covers R1 and R4.
- [ ] AC9: Focused tests, migration checks, Ruff, mypy, secret scan, report validation, and the
      non-integration suite pass locally and in GitHub Actions. Covers R5.
- [ ] AC10: The reviewed runtime passes the seven-case targeted gate three consecutive times and
      the bounded 12-case gate once. Covers R5.
- [ ] AC11: The same immutable runtime and dataset version pass all unchanged aggregate targets in
      three consecutive complete 40-case reports. Covers R5.
- [ ] AC12: The task records remaining synthetic-corpus, human-review, provider, cost, capacity,
      and production-evidence limitations without relabeling M5/M7 complete. Covers R5.

## Out Of Scope

- Replacing the current hybrid retrieval architecture or embedding model based on one 40-case run.
- Lowering quality thresholds, deleting failed evidence, rerunning until one pass is selected, or
  treating job retries as quality success.
- Storing raw model answers, prompts, evidence, citations, UUIDs, credentials, or provider bodies
  in reports, logs, metrics, or public events.
- Claiming production quality, production capacity, multi-node availability, or representative
  legal-domain coverage from the synthetic v2 corpus.
