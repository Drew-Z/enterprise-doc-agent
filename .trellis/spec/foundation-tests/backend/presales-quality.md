# Deployed presales quality evaluation

## Scope and entry points

`scripts/evaluate_presales_quality.py` separates live `run` from offline `score`.
The input fixture is `evaluation/presales_quality_v1.json`; gold is a separate file
bound to its exact SHA-256. Run never opens gold. Both use existing Presales API
schemas rather than creating another draft contract.
Git attributes preserve the exact fixture/response/prompt bytes, including CRLF;
the runner uses LF so its recorded hash is stable across checkout settings.

## Signatures

`python -m scripts.evaluate_presales_quality run --base-url HTTPS_ORIGIN
--object-host HOST --repeats 1|2 --output NEW_FILE` reads only `--input`.
`score --run RUN_FILE --output NEW_FILE` also reads `--gold` and makes no requests.
`collect(...)` permits separate injected control/object transports for tests;
`score(dataset_path, gold_path, report)` returns a reproducible mechanical report.

## Observable contract

- One or two fresh public demo sessions; at most six requirements each. Sequential
  generation, no model retry/failover, no administrator identity or modified quotas.
- Use normal multipart upload and document readiness before packet creation.
  The signed object PUT has a separate client without cookie or CSRF credentials;
  HTTPS host allowlisting and no redirects apply.
- Refuse an existing report before any request. Save each attempted row before and
  after dispatch, then retrieve its persisted result. Preserve failures. Unknown
  running outcomes or admission rejection stop collection without a new generation.
- Logout in finally; existing product expiry cleanup owns temporary demo tenants.
  Do not write cookies, CSRF, signatures or arbitrary exception/HTTP bodies to disk.
- Offline scoring verifies dataset/gold hashes, exact requirements, source content
  hashes and applicability, then checks labels, original-text citations and required
  quote anchors. Missing planned rounds/rows remain in the denominator.
- Missing usage and unverified currency cost are null. Latency includes failed HTTP
  attempts. Keep assistant semantic review distinct from mechanical scores and from
  business approval. Never infer customer accuracy or SLOs from this short corpus.

## Tests

`tests/evaluation/test_presales_quality.py` uses HTTP MockTransport for browser API
and object-store boundaries; no external requests. Assert no automatic retry after
an uncertain response, credential separation, original input isolation, overwrite
protection, incorrect affirmative labels, fabricated quotes, conflict-side omission,
hash/source substitution rejection and unknown usage. These tests are not live
model observations. Live reports are explicit bounded runs outside normal CI.

## Validation and error matrix

| Condition | Result |
|---|---|
| Existing output | FileExistsError before network; preserve file |
| Wrong input/gold hash or source content | Reject offline scoring |
| HTTP 401/403/429 or unresolved running attempt | Stop, save partial result, logout |
| Timeout/502 followed by terminal row | Retain HTTP failure plus persisted outcome |
| Failed or missing planned row | Keep it in the score denominator |
| Absent token usage or price | null; never manufacture zero cost |

## Good, base and bad cases

Good: two independent demo enterprises with frozen inputs and separately reviewed
original drafts. Base: MockTransport verifies collector behavior without model
quality claims. Bad: retry until success and omit failed attempts from statistics.

## Wrong versus correct

Wrong: reuse a logged-out demo actor for a prompt comparison and score its empty
retrieval as model performance. Correct: keep a newly owned diagnostic session
active until its reads finish; assert nonempty evidence before model dispatch.
Logout retires access through the existing cleanup lifecycle. Never reactivate a
retired tenant or weaken authorization for a test. A read-only terminal snapshot
may reconcile explicitly owned attempt IDs, preserving the original HTTP report.
