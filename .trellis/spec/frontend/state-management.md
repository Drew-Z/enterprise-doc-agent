# State Management

## State Categories

- Server state: TanStack Query.
- Derived display state: booleans derived from the query result.
- Local UI state: M1 upload execution uses a feature-local pure reducer.
- Global client state: none is introduced in M0.

## Server State

The shared `QueryClient` disables retries and focus refetch by default. The readiness
query sets its own stale time and polling interval. A typed 200 is healthy, a typed
503 is degraded, and network/schema/unexpected-status failures are unreachable.

## Promotion Rule

Introduce global state only when multiple unrelated routes require the same mutable
client-owned value. Server-owned resources remain in TanStack Query.

## Multipart Upload Execution

The upload reducer returns typed effects and never executes Worker, fetch, XHR,
scheduler, or storage calls. `generation` rejects messages from paused, canceled, or
replaced work; each part also carries an `attempt` so a late retry result cannot mutate
the current attempt.

Pause resets browser-owned active parts and aborts local requests without changing the
server session. Cancel additionally emits the server abort effect. A stale create
success after cancel emits a compensating abort so quota is not left to expiry alone.

The bounded scheduler is separate from the reducer. It starts at most four parts,
releases every terminal outcome, and holds a newer generation of the same part behind
an older aborting task.

## Common Mistakes

Do not copy query data into local state, treat HTTP 503 as a transport exception, or
invent a fifth backend state for the refresh button.

## Proven Examples

- `apps/web/src/main.tsx`
- `apps/web/src/App.tsx`
- `apps/web/src/App.test.tsx`
- `apps/web/src/upload/state/reducer.ts`
- `apps/web/src/upload/state/scheduler.ts`
