# Frontend Development Guidelines

These guidelines record the React/Vite conventions proven by the M0 readiness
dashboard.

## Guidelines Index

| Guide | Scope | Status |
|---|---|---|
| [Directory Structure](./directory-structure.md) | Source ownership | Adopted in M0 |
| [Component Guidelines](./component-guidelines.md) | Operational UI components | Adopted in M0 |
| [Hook Guidelines](./hook-guidelines.md) | TanStack Query usage | Adopted in M0 |
| [State Management](./state-management.md) | Server and local state | Adopted in M0 |
| [Quality Guidelines](./quality-guidelines.md) | ESLint, TypeScript, tests | Adopted in M0 |
| [Type Safety](./type-safety.md) | Runtime boundary validation | Adopted in M0 |

Future document and Agent interfaces must extend these guidelines only after their
real code and tests exist.

## Proven Examples

- `apps/web/src/App.tsx`
- `apps/web/src/api/health.ts`
- `apps/web/src/App.test.tsx`
