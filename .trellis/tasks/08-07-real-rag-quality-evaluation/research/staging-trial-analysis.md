# Staging Real-Provider Trial Analysis

## Execution Boundary

- Execution date: 2026-08-07.
- Source baseline: `55d3fafe6e162165d77d028b07a0c141c8780717` with an explicitly dirty
  working tree containing the new evaluator and synthetic dataset.
- Control plane: staging API through in-cluster loopback. The first attempt through the
  public Cloudflare route timed out during polling, so the successful bounded run avoided
  that unnecessary hairpin.
- Object storage: real staging R2 HTTPS presigned upload and download paths.
- Chat route: `openai_compatible` / `grok-4.5`; provider model revision was not exposed.
- Behavior versions: graph, prompt, and tool schema were all `m4.v1`.
- Dataset SHA-256: `5684b055f2dd2e5eb0c148ce6383cbc983a1b68a8fb41ae0b9e4f85205514135`.
- Corpus SHA-256: `c6887a0ca112cd62499c9d61d5caa9d87e94a4ba6c8770293f9f3a8c6cf54e36`.

The report stores hashes, label IDs, stable anchor IDs, public route identity, latency, and
finite error codes. It does not store tokens, endpoints, presigned URLs, runtime UUIDs,
document bodies, queries, or complete answers.

## Single-Case Health Check

`fact-proc-manager` completed successfully and matched its required fact and citation
anchor. After correcting uncovered refusal metrics to `null`, the single-case aggregate is
intentionally `failed`: a fact-only health check cannot satisfy refusal-quality targets.

## Bounded 12-Case Trial

- Passed cases: 8 of 12.
- Fact recall: 0.70.
- Closed-label fact precision: 0.70.
- Grounded fact rate: 0.70.
- Citation precision: 1.00.
- Citation recall: 0.95.
- Refusal precision: 1.00.
- Refusal recall: 0.50.
- Refusal reason accuracy: 0.50.
- Latency: P50 44.25 seconds, P95/P99 59.20 seconds.
- Error codes: one `empty_evidence`, one `citation_required`.

The bounded trial failed the configured quality targets. It is evidence of a functioning
real staging path and of current quality gaps, not evidence that M5 or M7 passed.

## Failed Cases Requiring Human Review

1. `fact-employee-vacation`: the answer was two characters, cited the correct
   `emp.vacation` anchor, but did not match the longer accepted text variants. This may be a
   correct terse numeric answer or a generation error; the sealed report alone cannot decide.
2. `fact-retention-customer`: the answer was seven characters, cited the correct
   `ret.customer` anchor, but did not match an accepted fact variant. This may be strict label
   wording rather than incorrect semantics and must be reviewed before changing labels.
3. `hard-support-objectives`: the answer cited only `support.rto`, missed the required
   `support.rpo` anchor, and matched neither closed-label fact. This is the strongest retrieval
   or answer-completeness failure in the sample, but semantic review is still required.
4. `refuse-contract-stock-price`: the run ended as `failed` with `citation_required` instead
   of a supported refusal status and accepted refusal reason. The policy/error taxonomy should
   decide whether this status is converted to a refusal or remains an execution failure.

## Remaining Gate Work

- Human-review the four failed cases without persisting raw answers in repository evidence.
- Correct evaluator labels only when a reviewer confirms a semantic false negative.
- Correct retrieval, prompt, or refusal-policy behavior only when the failure is reproducible.
- Run all 40 cases multiple times with frozen behavior versions.
- Add a separately approved representative enterprise corpus and reviewed labels.
- Expose provider revision, token usage, cost, fallback count, and breaker state.
