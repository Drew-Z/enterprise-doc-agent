# UI Redesign Notes

## Decision

The first UI was functionally correct but presented the product as a local
foundation smoke console. The redesign keeps the upload state machine, Agent
SSE stream, approval flow, and readiness query unchanged while changing the
product shell and information hierarchy.

The new shell is intentionally closer to a mature enterprise knowledge product:

- persistent workspace navigation on desktop;
- a global search / Agent command entry point;
- a business-facing page title before infrastructure details;
- documents and Agent runs as the primary work areas;
- runtime health as a secondary system surface;
- a compact mobile header with the same two work areas.

## Reference research

The direction was informed by public product pages, not copied from a single
brand:

- [Glean](https://www.glean.com/) emphasizes enterprise search, permission-aware
  context, Agent orchestration, and governance.
- [Notion AI](https://www.notion.so/product/ai) combines enterprise search,
  custom Agents, usage/analytics, granular permissions, and verified pages.
- [Microsoft SharePoint](https://www.microsoft.com/en-us/microsoft-365/sharepoint/collaboration)
  emphasizes intelligent search, AI-ready content, custom Agents, content
  freshness, workflows, and administration.

The project borrows the interaction patterns that fit this product: search-first
entry, explicit provenance/governance surfaces, and progressive disclosure. It
does not borrow their branding, logos, copy, or visual assets.

## Implemented direction

The visual direction is now a graphite operations console: a dark navigation
rail, a cool-white work surface, cobalt primary actions, and compact neutral
cards. This gives the product a more industrial operations feel than the
previous teal-on-white treatment while keeping the same information density
and state semantics.

The React implementation is in:

- `apps/web/src/App.tsx`
- `apps/web/src/product/routes.ts`
- `apps/web/src/product/DocumentsPage.tsx`
- `apps/web/src/product/RuntimeOverview.tsx`
- `apps/web/src/product/product.css`
- `apps/web/src/styles.css`

The redesign uses the existing Lucide icon dependency and keeps the existing
component APIs and test selectors stable. The captured desktop and mobile
verification images are local runtime artifacts under `apps/web/tmp/screenshots/`
and are intentionally ignored by Git.

The application is no longer a single scrolling workbench. It exposes five
shareable hash routes:

- `#/overview` for workflow orientation and primary actions;
- `#/documents` for tenant-scoped document inventory and ingestion entry;
- `#/agent-runs` for durable, approval-aware Agent execution;
- `#/audit` for tenant-scoped governance events and trace review;
- `#/runtime` for dependency readiness and operator diagnostics.

The current refinement also adds two product-level interaction surfaces:

- a three-step `Ingest -> Reason -> Review` workflow strip, making the document
  lifecycle explicit before users enter upload or Agent controls;
- a keyboard-friendly command search (`Ctrl K`) with actions for documents,
  Agent runs, and runtime readiness.

The final visual pass also normalizes interactive states around the cobalt
action token: focus rings, search focus, secondary-button hover, table-row
hover, and command-result hover now use the same blue family as primary
actions. Semantic green, amber, and red remain reserved for runtime and
workflow status, so action affordances are not confused with health states.

These choices are informed by fetched official product pages for [Glean](https://www.glean.com/),
[Notion AI](https://www.notion.so/product/ai), and [SharePoint](https://www.microsoft.com/en-us/microsoft-365/sharepoint/collaboration):
search and Agent entry should be adjacent, source and governance states should
be visible, and content operations should expose freshness or readiness without
overwhelming the primary task. The implementation does not copy their branding
or assets.

### Documents as an enterprise asset surface

The Documents page separates the formal asset workflow from local development
controls. Its default surface includes:

- ready asset, indexed version, and access-scope summaries;
- search, lifecycle-status filters, and refresh controls;
- processing, ready, and failed lifecycle states;
- version, update time, size, ingestion stage, and stable error-code metadata;
- a `Use in Agent` handoff into the Agent run workspace.

Upload controls and local JWT entry are progressively disclosed in a right-side
development drawer. This keeps an implementation detail out of the normal
business workflow while preserving a fully usable local demo.

The page is backed by a formal tenant-isolated read API:

```text
GET /api/documents?limit=200
```

The response exposes document/version and latest ingestion-generation status,
but deliberately omits document bodies, object keys, and content hashes.

## Design-system gap analysis

The code now defines a small semantic token layer for canvas, surfaces, borders,
text, cobalt primary, success, warning, danger, radii, and panel elevation. Inter remains
the product font, matching the existing Figma frames.

The connected Figma file currently contains editable static frames but no local
variable collections, text styles, effect styles, or reusable components. Code
Connect mappings are also not present in the repository. A production-quality
Figma library therefore still needs Foundations first, then reusable navigation,
button, status, field, metric, and table-row components before the five screens
can be considered design-system-backed.

## Figma handoff

An editable Figma capture of the redesigned desktop page was created here:

<https://www.figma.com/design/LTP7yu8zxacY9r5LQXvEBJ?node-id=1-2>

The capture is a raw layout reference. It is useful for reviewing spacing,
hierarchy, color and responsive intent; it is not treated as a replacement for
the React implementation or as a published design-system component library.

The Figma MCP Bridge is connected to the target file. The main Operations frame
includes the workflow strip, and separate frames document the command-search
interaction and workflow concept. These are editable design nodes, not flattened
screenshots.

The connected Figma file now also has an editable `ED / Design system handoff`
frame created through the Figma MCP Bridge. It documents the code-aligned color
tokens, spacing/radius guidance, reusable interaction patterns, and the five
product surfaces: Overview, Documents, Agent runs, Audit log, and Runtime health. This is
an implementation handoff board rather than a published team library: the file
still has no local variable collections, text/effect styles, or Code Connect
maps.

The React product has since added a sixth Identity surface for tenant member
and external subject-binding administration. That screen is implemented and
responsive, but it has not yet been added to the five-surface Figma handoff;
the code and verified screenshots remain its current visual source of truth.

The cloud library-discovery path remains blocked by the Figma Starter access/
quota boundary, and the Bridge connection is session-scoped. The project does
not claim a published Figma design system until the file can be reopened with
editor access and the Foundations/components are validated with metadata and
screenshots. React remains the source of truth for behavior and responsive
states.

The handoff frame was rechecked after the final theme pass: panel corners now
use `6px` and button specimens use `4px`, matching the production CSS. A final
export is available at `D:/workspace4Cursor/offer/figma-final-handoff.png`.

## Validation

- Frontend tests: `192 passed` across `31` files.
- Documents API and inventory-service focused tests: passed.
- Non-integration backend regression: `1006 passed, 125 deselected` (validated 2026-09-05).
- TypeScript project check: passed.
- Frontend ESLint: passed.
- Ruff format/check for the changed backend files: passed.
- `mypy packages/core/src apps/api/src`: passed.
- Vite production build: passed.
- Vite output uses a dedicated vendor chunk (`133.64 kB`) and keeps the main
  application chunk below the 500 kB warning threshold (`395.66 kB`).
- Latest route review covers Overview, Documents, Agent runs, Audit log, Identity, and Runtime;
  the new Identity member/binding control plane was checked at `1440x1000` and `390x844`.
- The Identity mobile review caught and fixed a high-specificity grid override that compressed
  directory actions; the follow-up Chromium screenshot has no visible overlap or clipping.
- Command search interaction covered by `App.test.tsx`.

`pnpm quality` passes on the current working tree. The separate integration suite
also passes with `125 passed, 1004 deselected`; `web-e2e` installs Chromium and runs
the upload-recovery and Agent approval/download workflows. Generated `.tmp-*`
evidence scripts are excluded from Ruff so one-off operator artifacts do not affect
the product source quality gate.

## Productization backlog

The answer-and-citations detail view now presents final answer, source excerpts,
page/location metadata, grounding verdict, approval state, and artifact lineage
in one reviewable surface. That result-review surface is now
implemented for visible `answer` artifacts; it reads a tenant-authorized,
hash-verified preview and renders citations without exposing private storage
locations. The same run now exposes a compact execution-provenance strip with
model/version, token usage, provider calls, repair/fallback counts, breaker state,
and execution sequence. The server now enforces restricted-document ACL and auditable
user/role grants, and Documents exposes those controls in a responsive policy drawer. The
larger enterprise backlog remains batch IdP/SCIM synchronization, first-login provisioning and end-to-end SSO acceptance, WORM/cross-region archival evidence, external connectors,
and full ABAC/PDP support. Real owner/member browser evidence already covers the restricted-document grant and revoke path.
