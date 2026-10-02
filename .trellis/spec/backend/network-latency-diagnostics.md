# Network latency diagnostics

Use this when remote database or object-store timings remain slow after a
verified query or read-count reduction. A lower operation count alone does not
establish a lower end-to-end p95.

- Bind samples to deployed images, settings hashes, execution location and a
  finite request/time budget. Distinguish serving HTTP, isolated ASGI, observer
  and public-browser measurements.
- Measure SQL, connection holds, object operations, admission and background
  execution separately. Do not print query parameters, credentials or signed URLs.
- Compare small and larger constant `SELECT` requests inside an explicitly
  verified read-only transaction before attributing latency to business indexes.
  A fixed synthetic SQL comment can vary request size without accessing user data.
- On Linux, collect per-connection TCP_INFO before and after each sample. Record
  retransmission deltas, RTT, MSS and TCP_NODELAY. Retransmissions demonstrate
  transport delay; they do not identify a particular physical loss or delayed-ACK hop.
- Keep port, prepared-statement, resolved-address, host-versus-Pod and per-socket
  packet-size comparisons distinct. Preserve transient good windows and subsequent
  failures. Retain TLS hostname and SSL mode when selecting a currently resolved
  address; never pin a cloud-service address based on one sample.
- An experimental opaque TLS relay must bind only a loopback ephemeral port,
  connect only to the approved target, limit accepted connections, avoid inspecting
  plaintext and close sockets/listeners in `finally`. It is diagnostic evidence,
  not a production proxy recommendation.
- Do not change host MTU, offload settings, routing, connection pools or database
  endpoints until a discriminating experiment supports the change and a concrete
  recovery plan exists. Derive the target from the approved current configuration;
  historical project identifiers can be stale.
- A successful diagnostic command means its assertions ran. It does not mean the
  performance target passed. A new API batch and full capacity matrix must retain
  their own budgets, denominators and stop conditions.
- A packet-header capture must select only the diagnostic connection and exclude
  payload output. ACK/SACK gaps narrow the failure evidence but do not identify
  the physical fault. Kernel capture drops and transport retransmissions differ.
- An owned edge relay experiment must have a fixed target, authentication,
  absolute expiry, byte/time limits and verified cleanup. Preserve end-to-end
  database TLS with its original hostname and an authoritative CA; never treat a
  certificate verification error as a reason to disable verification. Compare
  ordinary queries as well as large requests, and identify which TCP segment is
  observable. A working relay may still be too slow for the application target.
- Some resource DELETE endpoints return a successful empty response. Inspect the
  HTTP contract and authoritative resource state before retrying a mutation;
  parsing failures do not prove that deletion failed.
- A prepared-statement comparison must actually exceed the preparation threshold
  for the identical SQL and parameter types, then confirm the session catalog.
  Two executions with the default threshold of five do not exercise reuse.
  Separate first preparation from later reuse and retain per-sample TCP counters.
  Reduced warm constant-query latency is not proof of cold business admission
  latency, large result transfer, or a reliable physical network path.

## Proven Examples

Connection and pool configuration: `packages/core/src/enterprise_doc_core/db/engine.py`.
Documentation contract: `tests/foundation/test_documentation_contract.py`.

Diagnostic evidence: `docs/ops/database-transport-diagnosis.md` and the sanitized
`rc16-network-diagnosis.json` task record. The rc.16 comparisons reproduced TCP
retransmissions and rejected simple MTU, pooler-port and DNS-peer explanations;
they did not establish a deployed transport fix.

The subsequent `network-alternative-validation.json` record rejected per-socket
rate limits and an owned Cloudflare relay as performance fixes. The relay worked
with the official Supabase CA, but increased ordinary-query latency; all temporary
resources were removed after the bounded experiment.
