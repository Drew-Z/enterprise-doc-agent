# Frontend Type Safety

## Type Ownership

The health boundary module owns readiness types. The upload boundary owns API schemas,
Worker protocol messages, reducer actions/effects, persistence records, and XHR failure
categories. Component-only metadata remains local to its component.

## Runtime Validation

Fetch results begin as `unknown`. `isReadinessResponse` validates the object,
overall status, all required component names, and every component status before the
payload reaches React.

Multipart control-plane JSON uses strict Zod schemas. Worker requests and responses use
an exact-key, versioned runtime protocol. Browser persistence is parsed from `unknown`
through a strict versioned schema before it reaches reducer state.

## Patterns

Use string unions and records for closed contracts. Use small type guards for simple
external JSON and strict schemas for nested business protocols. Let TypeScript infer
local values only after a validated boundary.

## Forbidden Patterns

Do not cast an unvalidated response directly to the target interface, use `any`, or
accept missing readiness components as healthy.

## Proven Examples

- `apps/web/src/api/health.ts`
- `apps/web/src/App.tsx`
- `apps/web/tsconfig.app.json`
- `apps/web/src/upload/api/schemas.ts`
- `apps/web/src/upload/hashing/protocol.ts`
- `apps/web/src/upload/persistence.ts`
