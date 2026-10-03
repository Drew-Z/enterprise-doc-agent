# Queue observation independent of the personal computer

Continues CO-4 in the existing authorized commercial acceptance task. Baseline:
`cb9abc44cf0c28c7fc6469e1cf1f2a4537a35533`; private recovery records remain in the
existing commercial-production-readiness recovery group.

The Worker owns queue/Redis sampling. Add a read-only internal `/health/queue`
projection with only `healthy` and the oldest source timestamp in Unix milliseconds.
Both actual source observations must have succeeded, be no older than 45 seconds,
not predate this runtime, and be no more than 15 seconds ahead. Queue age above
120 seconds is unhealthy. Missing/failed/stale data never becomes an empty queue.
Worker progress failure also makes the projection unhealthy. No business or metrics
body is exposed, and the existing Kubernetes readiness/liveness rules stay intact.

An optional API background reader polls the fixed Worker Service every ten seconds,
with a two-second total deadline, one bounded JSON response and no credentials,
redirects, proxy or retries. It publishes the validated projection as optional
`queue` data on the existing public `/health/ready`; it does not change dependency
readiness or perform network work in that request. Failed polling replaces previous
success; source timestamps survive forwarding and expire even if the poller stops.
Only API-to-Worker port 8081 is added to network policy. This Service route is for
the current single Worker replica; multi-replica proof requires per-replica discovery.

Cloudflare optionally requires this projection while using the same readiness GET,
three/two streak, D1 incident state and notification history. The old heartbeat
remains required if also configured; switching it off is a separate verified rollout
step. The API class and monitor defaults stay disabled; the 4C4G manifest enables
the API reader. Activate the external requirement only after actual single-replica
source, network policy and API projection are verified in order.
Existing Windows tasks remain running. No new credential, public administration
endpoint, supplier call, mail replay or historical backup deletion is introduced.

Behavior slices (tests use public HTTP/module boundaries; only network and time
are substituted):

- [x] Worker reports unknown -> fresh -> backlog/failure/stale -> recovery without
  altering dependency readiness; legacy gauge-only writes cannot claim freshness.
- [x] API polling preserves timestamps, rejects malformed/oversize/redirect/stalled
  responses, closes clients/tasks and stays independent of request readiness.
- [x] Cloudflare fails closed on missing/invalid/stale queue data, preserves both
  configured input requirements and verifies failure/recovery in real D1/workerd.
- [ ] Local checks, spec and rollout package; current deployment and runtime proof
  remain separate from candidate source validation.
