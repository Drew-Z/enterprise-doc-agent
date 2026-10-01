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

## Proven Examples

Connection and pool configuration: `packages/core/src/enterprise_doc_core/db/engine.py`.
Documentation contract: `tests/foundation/test_documentation_contract.py`.

Diagnostic evidence: `docs/ops/database-transport-diagnosis.md` and the sanitized
`rc16-network-diagnosis.json` task record. The rc.16 comparisons reproduced TCP
retransmissions and rejected simple MTU, pooler-port and DNS-peer explanations;
they did not establish a deployed transport fix.
