# State Management

## State Categories

- Server state: TanStack Query.
- Derived display state: booleans derived from the query result.
- Local UI state: none is required by the M0 dashboard.
- Global client state: none is introduced in M0.

## Server State

The shared `QueryClient` disables retries and focus refetch by default. The readiness
query sets its own stale time and polling interval. A typed 200 is healthy, a typed
503 is degraded, and network/schema/unexpected-status failures are unreachable.

## Promotion Rule

Introduce global state only when multiple unrelated routes require the same mutable
client-owned value. Server-owned resources remain in TanStack Query.

## Common Mistakes

Do not copy query data into local state, treat HTTP 503 as a transport exception, or
invent a fifth backend state for the refresh button.

## Proven Examples

- `apps/web/src/main.tsx`
- `apps/web/src/App.tsx`
- `apps/web/src/App.test.tsx`
