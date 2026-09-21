# Commercial entitlement periods and usage

## 1. Scope / Trigger

The local S4 ledger is extended by `09-14-saas-entitlement-administration` with
operator configuration, explicit lifecycle states and Presales/API error contracts,
and by `09-15-saas-tenant-usage-workspace` with authoritative resource summaries
and the owner usage page.
Core owns `billing/`; API owns the HTTP projection; the operator adapter is
`scripts/manage_tenant_entitlements.py`. The separately packaged private
`python -m enterprise_doc_core.operations` supports formal environment configuration
under existing host/cluster and database administrative authorization. It does not
provide public platform RBAC or payment integration. Web behavior is specified in
`../frontend/tenant-usage.md`.

Reuse `tenant_entitlements`, `usage_reservations`, `usage_events` and `audit_events`.
Revision `20260914_0026` is already applied; do not edit it. Initial storage/seat
entitlements remain separate and are not rewritten by commercial period configuration.

## 2. Signatures

```python
EntitlementAdministrationService(session_factory=..., clock=None)
await service.configure(tenant_id=..., operator=..., configuration=...)
await service.show(tenant_id=..., entitlement_id=..., operator=...)
await service.list(tenant_id=..., operator=..., limit=20)

EntitlementUsageService(session_factory=..., clock=None, reservation_ttl_seconds=900)
await usage.reserve_provider_request(tenant_id=..., operation_id=..., session=None)
await usage.settle_provider_request(tenant_id=..., operation_id=..., session=None)
await usage.release_provider_request(tenant_id=..., operation_id=..., session=None)
await usage.summary(tenant_id=..., now=None, recent_limit=20)
```

`GET /api/tenant-usage` remains owner-only. Presales generation still requires its
existing author/source authorization and `Idempotency-Key`.

`UsageSummary.resources: TenantResourceUsage` is a frozen value object, projected
as `TenantUsageResponse.resources: TenantResourceUsageResponse`.

CLI commands are `configure`, `list`, and `show`. All require `--tenant-id`,
`--operator`, and `--reason`; configure/show require `--entitlement-id`.
Configure additionally requires `--expected-version`, `--plan-code`, `--period-start`,
`--period-end`, and `--request-limit`; only `--execute` writes.

## 3. Contracts

- `EntitlementConfiguration` is frozen and rejects extra fields. The stable UUID
  `entitlement_id` identifies both the row and its retry receipt. `expected_version`
  is a strict integer in `[0, 2**31-2]`; the new version is `expected_version + 1`.
- `plan_code` matches `[A-Za-z0-9][A-Za-z0-9_.-]{0,79}`. Period timestamps require
  time zones, normalize to UTC, and define `[start, end)` with `end > start`.
  `provider_request_limit` is a strict integer in `[0, 2**63-1]`; zero is deliberate
  denial, while null/bool/negative/unbounded new configurations are rejected.
- Configuration is append-only. It locks the active Tenant, checks the latest
  version and all existing periods, then atomically appends the entitlement and
  one `billing.entitlement.configured` audit. Adjacent periods, future periods and
  gaps are allowed. New periods must not have ended when checked after the lock.
- A same-ID retry must match version, plan, period and limit. It returns the
  existing counters and `replayed=true`, including after that period has ended.
  It does not reset usage or append another audit. A cross-tenant reused global ID
  is a configuration conflict, never a replay of another tenant's data.
- `PlatformEntitlementOperator(operator_id, reason)` is a trusted adapter value,
  not a tenant PrincipalContext. Labels are bounded/nonblank and reject controls.
  The audit has actor_id=null and records these labels, receipt ID and configuration.
  Local labels are attribution, not evidence of production RBAC.
- Configure/reserve/settle/release/summary lock Tenant before child rows, using
  `SET LOCAL lock_timeout = '5s'`. They read the injected/default Python UTC clock
  after obtaining the lock. Presales already locks Tenant before packet/row and
  reuses its transaction. The earlier Presales access lock retains its own contract.
- Usage-owned transaction scopes convert DBAPI failures, including commit-time
  failures, to `usage_store_unavailable`. A supplied session stays caller-owned;
  the caller still controls its final commit/rollback.

| `entitlementStatus` | Meaning | `enabled` | Remaining without a current period |
|---|---|---|---|
| `legacy` | No entitlement has ever been recorded | false | null |
| `active` | A period contains the current time | true | Computed from that period |
| `inactive` | Configured before, but expired, scheduled or between periods | false | 0 |

No-current-period responses have null plan/version/period/limit, zero current-period
counters, an empty event list and unknown cost. They are not a history view. The
operator's tenant-scoped show/list exposes recorded periods; list is version-descending
and bounded to 1–100 rows. Read-only show/list can inspect inactive tenants.

Every summary state also returns actual workspace resources. After the existing
Tenant lock, read its current `quota_bytes`, `used_storage_bytes` and
`reserved_storage_bytes`; do not use the admission-time storage snapshot. Reuse
`identity.seats.membership_seats` for active membership counts and the nullable
initial seat limit. This counts active owner/member memberships, without an extra
User.is_active filter; pending invitations do not occupy seats. No initial
entitlement means an unknown/unconfigured seat limit, not zero seats.

HTTP `resources` has `storageLimitBytes`, `storageUsedBytes`, `storageReservedBytes`,
`storageRemainingBytes`, `seatsUsed`, `seatLimit`, and `seatsRemaining`. Storage
remaining subtracts both used and reserved bytes. Seat remaining is null when the
limit is null, otherwise `max(0, limit - active)`. The authenticated Principal
selects the tenant; request query parameters cannot override it. No table or
migration is added for this read model.

`costStatus=known` means that at least one event in the current period has a
non-null estimated cost. It does not mean all costs or a full-period total are
known. Pydantic returns Decimal amounts as strings, including possible `0E-8`;
unknown amounts and currencies remain null.

New reserves on inactive entitlements fail. Existing reservations continue to replay,
settle or release against their original entitlement; their own TTL still applies.
The next period never inherits old counters. Lazy expiry cleanup visits the selected
period; there is no new background sweep of closed periods.

Presales reserves in the attempt-creation transaction. A rejection rolls that attempt
back before retrieval/provider dispatch. A successful validated draft consumes once;
failure/cancellation releases the reservation. This local quota policy does not mean
a failed or unknown upstream request cost nothing. Provider observations and nullable
cost fields remain separate from payments and customer-approved pricing.

CLI settings come from `FoundationSettings` (`APP_ENV`, `DATABASE__URL`, including
the repository `.env`). Only local/test and loopback hosts are accepted; URL query
host/hostaddr/service/servicefile overrides are rejected. Default configure returns
`status=preview, databaseValidated=false` without creating an engine. It does not
claim to have checked database versions, overlaps or current time.

Successful execution/query returns `status=confirmed`. An execution failure returns
exit 1 and `status=not_confirmed` for configure, with tenantId/entitlementId retained;
query failures use `status=failed`. Invalid arguments/environment use exit 2.
Only stable codes are printed, never raw exceptions or unknown argument contents.
On uncertain acknowledgement, show the same tenant/ID before deciding to replay.

The formal adapter uses `entitlement configure|show|list` after the common explicit
environment/database target and operator/reason options documented in
`tenant-admission.md`. Its required process settings have no .env/URL default; old
local CLI guards remain unchanged. Configure defaults to databaseValidated=false
preview, list checks its 1-100 bound before connecting, and execution is bounded by
30 seconds. Required inputs, period validation, stable receipts and audit stay in
EntitlementAdministrationService; the adapter does not change quota or lifecycle rules.

Admission acceptance and period configuration are separate transactions. The initial
pilot must keep generation disabled until its configured active period is confirmed.
This operator workflow does not solve atomic paid onboarding: unconfigured tenants
still retain legacy behavior. A future self-service flow needs a separate design.

## 4. Validation & Error Matrix

| Condition | Stable outcome |
|---|---|
| Wrong operator value, blank/oversized/control-containing attribution | `entitlement_operator_forbidden` |
| Same ID with changed configuration | `entitlement_idempotency_conflict` |
| Stale expected latest version | `entitlement_version_conflict` |
| Intersecting half-open periods | `entitlement_period_overlap` |
| Already-ended new period | `entitlement_period_ended` |
| Missing/inactive Tenant on mutation | `usage_tenant_unavailable` |
| Conflicting database insert, including another tenant's global ID | `entitlement_configuration_conflict` |
| Absent tenant-bound show result / invalid list bound | `entitlement_not_found` / `entitlement_invalid_limit` |
| Configuration/query DB failure; shared tenant-lock DB failure | `entitlement_store_unavailable`; `usage_store_unavailable` |
| Inactive commercial period before generation | `usage_entitlement_inactive` → `presales_entitlement_inactive`, HTTP 403 |
| Insufficient request quota | `usage_limit_reached` → `presales_usage_limit`, HTTP 429 |
| Usage service error before dispatch | `presales_usage_unavailable`, HTTP 503 |
| Member requests owner usage | `tenant_usage_forbidden`, HTTP 403 |

Preserve the existing error envelope, requestId and no-store policy. Ordinary reads,
review and export retain their authorization rules and remain available after expiry.

## 5. Good / Base / Bad Cases

- Good: configure version 1, reserve once, lose a later CLI acknowledgement, show
  by tenant/ID and replay unchanged input; counters and audit stay intact.
- Base: an unconfigured legacy tenant retains compatibility; explicit zero in an
  active period rejects new generation without dispatch or a failed attempt row.
- Bad: treating an expired configured tenant as legacy grants unmetered generation;
  using a fresh receipt on every retry obscures recovery and can add unwanted periods.

## 6. Tests Required

- `tests/billing/test_entitlement_administration_integration.py`: real PostgreSQL
  version/idempotency races, no overlap, tenant isolation, atomic audit rollback,
  and first reserve waiting for first configuration to commit.
- `test_entitlement_lifecycle_integration.py`: expired/scheduled/gap states,
  lock-wait boundary, original-period settlement, legacy behavior and real database
  failure before commit with no residual reservation/counter changes.
- `test_entitlement_configuration_contract.py`, `test_entitlement_cli.py`, and
  `test_entitlement_cli_integration.py`: strict fields, preview without DB access,
  local boundaries, safe errors, real commit followed by lost acknowledgement and recovery.
- API tests verify camelCase states, owner/member access and HTTP 503. Presales
  PostgreSQL/ASGI tests verify 403/429, zero dispatch/attempt/ledger rows on rejection,
  and existing draft read/review/export/replay after expiry.
- Keep the earlier billing concurrency, expiry and settlement tests. All temporary
  database rows must be removed by their fixture-owned UUIDs; do not purge by prefix.
- `tests/billing/test_tenant_resource_usage_integration.py`: real PostgreSQL
  resources in all three states, current quota vs admission snapshot, active
  membership counting, null/full capacity and tenant isolation, plus real service
  through ASGI with owner/member and a forged tenant query parameter. Fixture-owned
  initial entitlements are removed before their restricted grant FK; owned Users
  and Tenants are cleaned precisely.
- `tests/admission/test_platform_operations_integration.py` adds real 0026 tables to
  the fixture-owned admission schema. It opens a company through admission, configures
  a period through the formal adapter, consumes one request, and replays the exact
  configuration. Verify one configuration audit, unchanged consumption, show/list and
  remaining quota. This is controlled local infrastructure, not a public pilot.

## 7. Wrong vs Correct

Wrong: `if current is None: return legacy`, or choosing `now` before a contended lock.

Correct: lock Tenant, read the clock, select the half-open period, then distinguish
no historical configuration from no currently active period. Keep the reservation's
stored entitlement ID when settling. Preserve rows during rollback; never delete
configuration to restore legacy access.

## Proven Examples

- `packages/core/src/enterprise_doc_core/billing/service.py`
- `packages/core/src/enterprise_doc_core/identity/seats.py`
- `apps/api/src/enterprise_doc_api/tenant_usage/router.py`
- `tests/billing/test_entitlement_lifecycle_integration.py`
- `tests/billing/test_tenant_resource_usage_integration.py`
