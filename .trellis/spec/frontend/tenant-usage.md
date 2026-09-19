# Enterprise usage workspace

## 1. Scope / Trigger

`#/usage` provides the current enterprise owner with generation capacity, storage,
member seats and recent usage events. App uses the live product session for access
and navigation. The browser session boundary continues to own selection, retirement,
logout and workspace cache clearing. This is local UI/API delivery, separate from
the S5 complete first-use journey or real IdP/model/customer acceptance.

## 2. Signatures

```typescript
fetchTenantUsage(credential: ApiCredential, expectedTenantId: string, signal?: AbortSignal): Promise<TenantUsage>
TenantUsagePage({ credential, tenantId, contextKey, canView, sessionPending, sessionError,
  showcaseMode, onSessionRefresh, navigate }: TenantUsagePageProps)
```

The client reads owner-only `GET /api/tenant-usage`, with `VITE_API_BASE_URL` handled
by the existing authenticated transport. Browser mode requires the application
origin. No new authentication store or persisted usage data is added.

## 3. Contracts

- Parse unknown JSON with strict Zod objects, nonnegative safe integer counts,
  timezone-bearing dates and cross-field resource/period consistency. The backend
  still supports int64; values beyond JavaScript's safe integer range produce a
  data error instead of silently rounded UI values. This is a current UI limit.
- `resources` contains `storageLimitBytes/storageUsedBytes/storageReservedBytes/
  storageRemainingBytes/seatsUsed/seatLimit/seatsRemaining`. Nullable seat limits
  remain unconfigured. Storage is displayed in binary units; exact bytes are in
  value titles. Do not count rows from document or member list pages.
- `entitlementStatus` distinguishes `active`, `legacy`, and `inactive`. No-current
  counters are API placeholders, not historical usage. Inactive does not identify
  whether the period is future, expired or between periods. Legacy still follows
  existing generation limits, and is not a named free/unlimited plan.
- Active remaining subtracts used and reserved capacity. Zero available may mean
  work is in progress, rather than all capacity being consumed. Dates display UTC
  and the period end is exclusive.
- Decimal estimates are nullable strings, with scientific notation accepted for
  database zero (for example `0E-8`). Show only individual amounts and their
  currency; missing currency is explicit. `costStatus=known` means some estimates
  exist, not all requests are priced. Never sum the latest 20 events into a period
  bill or render unknown cost as zero.
- Capture ApiCredential and request signal. Verify credential/expected tenant
  agreement before requesting; verify response tenant and abort state after JSON
  body parsing, since a switch may occur while the body streams.
- Query key is `["tenant-usage", tenantId, contextKey]`; App's context includes
  tenant, actor and authentication revision. No token/CSRF is in keys. Use retry
  false, gcTime 0 and staleTime 0, inheriting the root's disabled focus refetch.
  Manual refresh hides prior details while fetching; errors hide cached values.
  Unmount the query on permission/session loss, and key it on context changes.
- Members, unconfirmed sessions, signed-out users and showcase do not request
  usage. Showcase never supplies invented operational data. Errors use localized
  recovery text and retain requestId for support.

## 4. Validation & Error Matrix

| Condition | UI/client outcome |
|---|---|
| Session checking/unavailable | Status or retry-session notice; no usage request |
| Member / HTTP 403 | Owner-only notice; no old details |
| Browser HTTP 401 or session-specific 403/409 | Existing transport retires the workspace |
| HTTP 503 / network error | No cached detail; explicit refresh with requestId when available |
| Invalid JSON, unsafe integer or inconsistent totals | `tenant_usage_invalid_response`; no inaccurate data |
| Wrong credential/response tenant | `tenant_usage_context_mismatch`; no foreign data |
| Switch during fetch or body parsing | Abort/discard; never adopt a newer credential |
| No current period | State explanation, actual resources, no fabricated history |

## 5. Good / Base / Bad Cases

- Good test fixture: limit 100, used 24, reserved 6 -> available 70; 1 GiB storage,
  256 MiB used and 128 MiB reserved -> 640 MiB available. These are test data only.
- Base: legacy generation and an absent seat limit show unconfigured values while
  still displaying actual storage and active memberships.
- Bad: a 403 refresh leaves the old owner's plan visible, or a late tenant A
  response replaces tenant B's usage. Pending and error states must hide details.

## 6. Tests Required

- `tenantUsageApi.test.ts`: response shape and tenant validation, safe integers,
  null/exact costs, API errors, old headers and body-stream completion after switch.
- `TenantUsagePage.test.tsx`: three period states, unavailable/zero capacity,
  resources, costs, manual recovery, denial and delayed response isolation.
- `App.usage.test.tsx`: actual owner navigation, direct member URL, unconfirmed
  session and sign-out through HTTP boundaries. Do not mock Query internals.
- `auth/clientCoverage.test.ts`: new client uses context headers, same-origin,
  no-store and the captured session signal.
- `usage-e2e/usage.spec.ts` with `playwright.usage.config.ts`: Chromium desktop and
  mobile, keyboard refresh, Chinese/English, switch/role-loss/logout, member and
  showcase. It mocks auth/business HTTP; it is not a real IdP/API/DB or S5 test.
  Runs start only a dedicated Vite server on 5187, never reuse a server, and use
  a unique temporary output directory. Optional `TENANT_USAGE_E2E_ARTIFACT_DIR`
  saves review screenshots. Node test configuration includes `vite/client` types
  because fixture types reference the client contract; do not suppress type errors.

## 7. Wrong vs Correct

Wrong: `estimatedCost ?? 0`, `enabled === false` as proof of a free plan, or member
list length as seats. Wrong: displaying `query.data` after a failed refresh.

Correct: preserve nullable estimates and explicit lifecycle state, read authoritative
resources from the owner API, and render successful current-context data only when
the query is neither pending, fetching nor errored.

## Proven Examples

- `apps/web/src/product/TenantUsagePage.tsx`
- `apps/web/src/product/tenantUsageApi.ts`
- `apps/web/src/App.usage.test.tsx`
- `apps/web/usage-e2e/usage.spec.ts`
- `tests/billing/test_tenant_resource_usage_integration.py`
