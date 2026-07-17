# Component Guidelines

## Component Shape

M0 uses typed function components. `App` derives display states from one TanStack
Query result and renders compact operational sections. Repeated service metadata is
a typed array mapped into consistent cards.

## Props and Composition

Define explicit props when extracting a component. Keep data contracts in the
boundary module that owns them. Pass already validated values to display components;
do not make leaf components parse network payloads.

## Styling

Use the application CSS file with stable class names, constrained widths, explicit
grid tracks, and responsive breakpoints. Cards are reserved for repeated service
items. Icon-only actions use Lucide icons, an accessible label, and a tooltip title.

## Accessibility

Use semantic headings, `aria-live` for readiness changes, `role="status"` for
loading, `role="alert"` for unreachable state, and real buttons for actions.

## Common Mistakes

Do not render fake job/document data, place cards inside cards, use text-only refresh
controls, or let loading/error text resize fixed controls.

## Proven Examples

- `apps/web/src/App.tsx`
- `apps/web/src/styles.css`
- `apps/web/src/App.test.tsx`
