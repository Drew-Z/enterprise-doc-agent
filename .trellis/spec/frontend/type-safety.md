# Frontend Type Safety

## Type Ownership

The health boundary module owns `ComponentStatus`, `ComponentName`,
`ComponentHealth`, and `ReadinessResponse`. Component-only metadata remains local
to `App.tsx`.

## Runtime Validation

Fetch results begin as `unknown`. `isReadinessResponse` validates the object,
overall status, all required component names, and every component status before the
payload reaches React.

## Patterns

Use string unions and records for closed contracts. Use small type guards for external
JSON. Let TypeScript infer local values after a validated boundary.

## Forbidden Patterns

Do not cast an unvalidated response directly to the target interface, use `any`, or
accept missing readiness components as healthy.

## Proven Examples

- `apps/web/src/api/health.ts`
- `apps/web/src/App.tsx`
- `apps/web/tsconfig.app.json`
