# Staging Stock-Price Retrieval Diagnosis

## Scope

Read-only inspection of the latest staging runs for the 12-case bounded trial on 2026-08-07.
The query joined frozen Agent evidence to synthetic corpus chunks in memory and emitted only
case IDs, terminal/error labels, ranks, rounded RRF scores, and stable anchor IDs. It emitted no
runtime UUIDs, raw document text, answer text, endpoints, or credentials.

## Failed Case

- Case: `refuse-contract-stock-price`
- Terminal state: `failed`
- Error: `citation_required`
- Candidate count: 1
- Candidate rank/score: rank 1, RRF `0.01639344`
- Stable anchor: `contract.payment`

The candidate is about invoice payment terms and cannot support a supplier stock-price answer.
Its score is exactly `1 / (60 + 1)`, the default RRF acceptance floor. This indicates a rank-one
hit from one recall channel, not calibrated semantic confidence.

## Bounded-Trial Distribution

- The nine expected-answer/safety cases with candidates had top RRF scores from `0.03252247`
  through `0.03278689`, consistent with support from both keyword and vector recall.
- `refuse-security-ceo` produced no candidates and correctly finalized `refused`.
- `refuse-contract-stock-price` was the only trial case with a single-channel top candidate at
  `0.01639344`; it reached the model, which produced no citation, and the old graph failed it.

## Decision

Do not change the global RRF threshold in this remediation. Requiring a score above `1 / 61`
would reject the observed stock-price false positive, but the bounded sample contains only two
unsupported questions and does not establish recall impact for valid questions that rely on
semantic-only retrieval.

The explicit `insufficient_evidence` model outcome is required regardless of future retrieval
tuning. A separate retrieval calibration exercise should run all six unsupported cases plus a
representative valid set and compare at least these policies:

1. current one-channel rank-one acceptance,
2. cross-channel agreement for top candidates,
3. calibrated raw vector-distance thresholds by embedding revision,
4. a lightweight relevance/rerank gate with measured false-refusal cost.

Until that evidence exists, changing the threshold would overfit one synthetic failure.
