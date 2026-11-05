# Primary retry and fallback proposal — 2026-10-11

Status: reviewable design only. No live behavior, quota or dispatch limit changed.
Recommendation: retain two accounted requests per row, but choose the second slot
by failure type. A short transient primary error may use it for one delayed primary
retry. That consumes the fallback opportunity for the row. Primary retry followed
by fallback would require an explicitly different three-slot policy.

## What currently happens

The gateway issues one HTTP request. For an eligible failure, background recovery
uses the other route, because both `_next_route` and `_dispatch` prohibit repeating
the route or endpoint/model key within the attempt. `ExecutionPolicy.v1` further
requires distinct routes and `maxProviderRequests == len(routes) <= 2`.

Current retryable categories include408/426/429/5xx and transport/timeouts. A
dispatched invalid-model-output result is also eligible for fallback.400/401/403,
invalid citations, excessive input/output and authorization/delivery failures stop.
The HTTP error branch currently retains a code/retryable flag, not a normalized
Retry-After value or the exact transient-status classification needed below.

Row fallback is separate from channel health. Normal retryable failures accumulate
toward the circuit threshold; a single first failure normally does not globally
remove the primary. Output-contract rejection is not evidence of transport outage.
The current fallback also changes gpt-5.6-sol/v24 to Grok4.7/v15, so it is not simply
another connection to the same qualified behavior.

Pointers: `packages/core/src/enterprise_doc_core/presales/gateway.py:354`,
`background.py:53`, `background.py:416`, `background.py:458`, `policy.py:40`,
`policy_gateway.py:23`, and `route_health.py`.

## Proposed frozen decision table

All actions require remaining dispatch budget, an active authorized row, a valid
lease/fence and enough overall deadline. There is no retry inside HTTP transport.

| First outcome | Proposed use of remaining slot |
| --- | --- |
| Confirmed connection failure before request transmission; HTTP408/500/502/503/504 with no partial model output | Retry the same primary once after delay if healthy and admissible; otherwise use one qualified healthy alternative, or stop if none exists. |
| HTTP429 with a valid short Retry-After | Retry primary once no earlier than the server delay, within the5-second retry-wait ceiling and deadline. Honor the route cooldown and normal half-open admission. |
| HTTP429 without a usable delay, or Retry-After beyond the ceiling/deadline | Do not immediately retry that primary. Cool/admit it according to the bounded rate-limit policy and use a qualified available alternative, or stop. Missing delay uses the existing30-second circuit cooldown as the proposed conservative default. |
| Other5xx indicating unsupported capability, such as501/505 | No same-primary retry. Use a qualified alternative only if the failure is classified as a route capability problem; otherwise stop. Preserve the classified status for review. |
| HTTP426 / explicit route protocol incompatibility | No same-primary retry. Retain at least300-second cooldown; use a qualified alternative if available. |
| Timeout or broken stream after request transmission, uncertain transmission stage, partial model output, missing terminal receipt | Stop automatic dispatch and retain the original as uncertain/failed with unknown usage as appropriate. Generation may still be billable upstream. Do not treat interruption as proof of cancellation. |
| Complete response rejected by schema, citation or substantive validation | Preserve the rejected output/diagnostic and require review. Do not infer channel outage or automatically change model/protocol. Semantic defects cannot all be detected mechanically. |
|400/401/403, input/output bounds, authorization, cancellation, quota or delivery rejection | Stop; correct the applicable input/configuration/authorization issue through normal workflows. Do not apply blanket retry. |

“Qualified alternative” means a separately frozen model/protocol/source-binding
combination with current contract and semantic evidence for the intended workload.
The present Grok/v15 route does not satisfy a v25-equivalence claim. A previously
qualified same-model second channel is preferred when available; none is invented
or configured here. Different-model fallback remains possible only as an explicit
qualified policy decision. This stricter output-failure rule reduces automatic
recovery availability until a suitable alternative is qualified; that is an
intentional tradeoff for traceable answer behavior.

Delay proposal for a single retry:1 second plus a frozen/safely recorded0–250ms
jitter, or the larger valid Retry-After delay. Parse delta-seconds and HTTP-date;
negative/past dates become0. Invalid headers are treated as absent. Never shorten
a valid server delay to fit the5-second ceiling: if it is too long, skip this
retry. A parsed long delay may be retained as route not-before within a documented
bounded health policy; never store unbounded raw headers in user-facing telemetry.

Default row deadline stays360 seconds, primary route120 seconds and alternative180
seconds. Admit a second call only when the chosen delay, its full route timeout and
10 seconds for finalization fit the remaining row deadline. The first call reserves
enough time for the largest eligible second action; no third route or new660-second
deep-mode behavior is implied. Deadline/cancellation/lease is rechecked after delay.
Persist a scheduled retry time; do not keep a database transaction or worker sleep
alive while waiting. Late results cannot mutate a newer fenced attempt.

## Explicit accounting and compatibility work

This cannot be implemented safely by setting an HTTP `retries=1` option or deleting
one duplicate-route check. The required implementation is a versioned policy and
state transition change:

1. Add `presales.execution.v2` with a unique immutable route registry, a two-slot
   ceiling, allowed failure actions, per-route attempt limits, delay policy and
   deadline rules. Keep v1 parsing/execution unchanged for historical operations.
   In v2 only, allow the same primary route in two distinct numbered calls when
   the frozen decision explicitly permits that retry; do not permit arbitrary
   route cycles or a third call.
2. Preserve one call record per dispatched HTTP request, monotonic call numbers,
   endpoint/model/prompt identity, reason for route choice and delay. A second
   primary request consumes the same dispatch/token accounting as a fallback.
   Commit the reservation before network I/O; failed or unknown calls never count
   as zero. A delay alone is not a dispatch. Recheck daily admission at actual
   dispatch, including a UTC-day boundary.
3. Extend provider errors with a sanitized failure phase, retry classification
   and normalized delay. “Definitely not sent” must come from reliable transport
   evidence, not a generic timeout. If transmission cannot be distinguished,
   choose the uncertain branch. Do not log credentials, full headers or raw
   provider error bodies in product telemetry.
4. Keep circuit health independent of per-row attempt ordinal. Do not bypass an
   open circuit just because one row has a retry allowance; use existing single
   half-open admission/fencing. Model-output rejections do not count as network
   failures. A successful primary retry updates health through the normal path.
5. Charge/settle the user-visible operation once; preserve both provider-call
   records and known/unknown usage. Partial output, cancellation, worker death,
   takeover and late callbacks must not duplicate a result, settlement or retry.

Rollout must first deploy readers that understand both policy versions while still
creating v1 operations. Enable v2 only for newly created explicitly opted-in rows;
never reinterpret a frozen v1 attempt. Once v2 records exist, binary rollback to
an old v1-only reader is unsafe. The immediate rollback is to disable new v2
creation on the compatible reader and let existing operations follow their frozen
policy. Exact schema/history and signed release gates still apply.

## Acceptance before enabling

Use fake transport responses and real PostgreSQL to verify the matrix, without
paying for network faults. Check408/429/date-or-seconds Retry-After/503/426, short
and excessive waits, pre-send failure, uncertain timeout, partial stream, invalid
output/citation, authorization and cancellation. Assert actual HTTP counts and
route order, no hidden transport retry and no third request.

Also verify restart/takeover during scheduled delay, circuit-open and half-open
contention, concurrent daily-limit admission, UTC rollover, deadline expiry,
late-result fencing, exactly-once final settlement, v1 historical rendering and
compatible v2-disable rollback. A real-primary smoke is a separately frozen finite
acceptance step; do not deliberately flood a provider to create429 errors.

Public-questionnaire expansion materials remain available for a later quality
comparison, but four generation-only v25 observations do not qualify retry policy
or an alternative model. This proposal is complete for review; implementation,
live qualification and activation remain separate work.
