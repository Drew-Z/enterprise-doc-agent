# Frontend Quality Guidelines

## Required Gates

Run from the repository root:

```powershell
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

Dependencies are installed with `pnpm install --frozen-lockfile`. CI runs these
commands in an independent frontend job.

## Testing

Use Vitest and Testing Library at the HTTP boundary. Tests cover loading, typed healthy
200, typed degraded 503, network failure, schema failure, and manual recovery. Query
retries are disabled so tests and operational behavior expose the first real result.

Upload modules add deterministic Vitest coverage for bounded hashing, Worker lifecycle,
strict API/runtime schemas, XHR setup and event failures, the complete reducer command
matrix, four-way scheduling, recovery identity, and secret-free persistence. Worker and
XHR tests use injected narrow fakes; the jsdom suite does not pretend to execute a real
browser Worker or object-store request.

## Review Checklist

Check runtime schema validation, semantic status/alert roles, keyboard-accessible
actions, stable responsive dimensions, no overlap at desktop/mobile widths, and no
fake operational data.

## Forbidden Patterns

Do not mock TanStack Query internals, use `any`, suppress TypeScript errors, add
allow-failure CI, or ship an unreviewed loading/error layout.

## Proven Examples

- `apps/web/src/App.test.tsx`
- `apps/web/src/test/setup.ts`
- `apps/web/eslint.config.js`
- `.github/workflows/quality.yml`
- `apps/web/src/upload/**/*.test.ts`
