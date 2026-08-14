# Local Diagnostic Slice Validation - 2026-08-12

## Scope

This record covers only the secret-safe diagnostic implementation. It does not classify the seven
staging cases, approve a semantic remediation branch, or claim that the RAG quality gate passes.

## Results

- Focused Core, Worker, API, evaluator, and migration tests: `92 passed`, `1 deselected`.
- Report and staging-secret validation tests: `36 passed`.
- Ruff format: `319 files already formatted`.
- Ruff check: passed.
- Mypy strict source check: `131 source files`, no issues.
- Full non-integration suite: `818 passed`, `107 deselected`.
- PostgreSQL integration checks: migration round trip, Job diagnostic persistence, and Agent status
  projection all passed (`3 passed`).
- Local PostgreSQL migration state after the checks: `20260811_0012 (head)`.
- Existing sealed reports checked: `26`; invalid payload seals: `0`.
- Immutable baseline files checked: `31`; SHA-256 mismatches: `0`.
- Repository credential-signature scan: `0` matches for private-key, common cloud-token, bearer JWT,
  and common API-key signatures.

## Review Findings Resolved

- The Agent execution boundary now accepts a diagnostic only from typed
  `GroundingValidationError`; arbitrary exceptions cannot inject a valid-looking diagnostic.
- MCP text classification accepts only an exact allowlisted code or the exact FastMCP wrapper for
  the requested tool. Message text that merely mentions a code and wrappers for another tool
  collapse to `mcp.<known_tool>.returned_error`.
- The nullable diagnostic is tested through storage, Job status, Agent status, and staging report
  projection. Public error codes and retryability remain unchanged.

## Remaining External Gates

- GitHub Quality has not run for this working tree because the implementation is not committed or
  pushed yet.
- No immutable diagnostic image has been built or deployed from this change.
- The seven selected real-staging cases have not run with the new evaluator/runtime diagnostics.
- The quote-count answer and unresolved payment citation still require authenticated human review.
- No dataset, prompt, grounding authorization, MCP behavior, retrieval threshold, `top_k`, or RRF
  semantic change is approved from local evidence alone.
- Representative enterprise/legal documents, independent human review, provider revision
  stability, cost, production-like capacity, managed observability, multi-node recovery, and
  GPU/vLLM evidence remain outside this local slice. M5 and M7 remain open.
