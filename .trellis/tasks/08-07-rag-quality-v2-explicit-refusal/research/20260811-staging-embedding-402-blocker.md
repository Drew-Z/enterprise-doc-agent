# Staging Embedding 402 Blocker

## Scope

This note records why the targeted run after repeat 9 did not produce a RAG quality report. It is
an operational diagnosis, not a replacement or synthetic reconstruction of evaluator evidence.

## Confirmed State

- Release `v0.1.24` is deployed in staging.
- API has two ready replicas; Worker, Consumer, Web, Redis, and Prometheus are ready.
- New release Pod restart counts are zero.
- Agent execution uses a five-attempt staging retry budget.
- Embedding configuration is `Qwen/Qwen3-Embedding-4B`, 1024 dimensions.
- `20260811-rag-quality-v2-remediation-repeat-9.json` passed all four targeted cases and its
  payload seal verifies.
- The current consecutive targeted-pass count is one because repeat 8 is an immutable failed
  provider run.

## Failure Timeline

The local public Cloudflare path first reset TLS connections to the staging control plane and
then to R2. The staging service and R2 endpoint remained reachable from the server, so a
loopback-only SSH relay was used to isolate that transport issue. Both relayed control-plane
requests and an HTTPS object-store handshake succeeded.

The relayed evaluation then uploaded fresh corpus objects and began polling the authenticated
`ready-document-versions` endpoint. Consumer logs showed three real ingestion tasks calling the
configured embedding endpoint and receiving `HTTP/1.1 402 Payment Required`; each ingestion task
terminated as failed. The evaluator continued polling because none of the new document versions
could become ready and was stopped at the 900-second boundary.

No sealed repeat-10 report exists. The attempt did not reach the four Agent quality cases and
must not be counted as either a passing or failed quality repeat.

## Required Recovery

1. Restore quota for the configured embedding provider or approve a replacement OpenAI-compatible
   embedding URL and key.
2. Keep the model contract at 1024 output dimensions unless a separate migration is planned.
3. Update the staging GitHub variables/secrets, redeploy the affected workloads, and verify one
   real embedding request without printing credentials or provider response bodies.
4. Start a fresh numbered targeted report and continue sequentially until three consecutive
   reports pass.
5. Only then run the 12-case trial and 40-case full suite.

## Evidence Handling

The two local transport-failure logs were retained outside the repository for diagnosis. They
contain no quality report and are not committed as M5 evidence. Existing repeat reports remain
unchanged, including all real provider and MCP failures.
