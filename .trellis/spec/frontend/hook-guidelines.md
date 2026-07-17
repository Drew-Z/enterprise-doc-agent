# Hook Guidelines

## Current Pattern

M0 does not define a custom hook. The dashboard calls `useQuery` directly because
there is one small readiness query and no repeated stateful logic.

## Data Fetching

TanStack Query owns remote readiness state. Queries use stable keys, disable retries
for operational truth, and use explicit stale/refetch intervals. Manual refresh calls
`refetch`; it is an action rather than a separate server state.

Create a `use*` hook only when the same query configuration or stateful behavior is
reused by multiple components. Keep transport and runtime validation in `src/api`.

## Common Mistakes

Do not duplicate fetch logic in components, put server data in local `useState`, or
hide a failed health response behind automatic retries.

## Proven Examples

- `apps/web/src/App.tsx`
- `apps/web/src/api/health.ts`
- `apps/web/src/main.tsx`
