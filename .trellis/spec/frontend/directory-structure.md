# Frontend Directory Structure

## Layout

```text
apps/web/src/main.tsx        Application providers and root mounting
apps/web/src/App.tsx         Current operational overview
apps/web/src/api/health.ts   Health transport, types, and runtime validation
apps/web/src/styles.css      Application styles and responsive rules
apps/web/src/test/setup.ts   Shared Vitest DOM setup
apps/web/src/App.test.tsx    User-visible state tests
```

## Organization

Keep boundary clients under `src/api`. Keep provider setup in `main.tsx`.
Feature components stay near the feature until there is repeated, proven reuse.
Do not create speculative shared folders for M1-M7 features.

## Naming

React component files use PascalCase. Boundary and utility modules use lower-case
domain names. Tests use `.test.tsx` or `.test.ts`.

## Proven Examples

- `apps/web/src/main.tsx`
- `apps/web/src/api/health.ts`
- `apps/web/src/App.test.tsx`
