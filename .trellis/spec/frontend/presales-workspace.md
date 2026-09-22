# Presales workspace UI

Current route: `#/presales`, integrated with the existing App/session/navigation.
Implementation: `apps/web/src/presales`. Public API and server authorization facts
are documented in `.trellis/spec/backend/presales-workspace.md`.

The creation form uses the real document inventory, filters ready versions with
ready/succeeded generations, and asks the user to confirm source applicability.
Each pasted line is one requirement; an optional tab separates the location.
This is manual entry, not automatic splitting of a tender document.

## Entry from uploaded documents

`apps/web/src/product/DocumentsPage.tsx` refreshes the inventory every two seconds
while a successful query contains uploaded/processing documents. Ready/failed
terminal states and read errors stop automatic polling; a read error retains an
explicit retry. Showcase mode does not poll. The inventory query key includes the
current tenant/actor/auth revision supplied by App.

Desktop rows and mobile cards expose a response-sheet action only for a ready
version with a generation and a valid session callback. Disabled actions have a
distinct visual state. The entry carries one version ID bound to the current
identity. `apps/web/src/App.tsx` consumes it once and clears it on identity changes.

`PresalesWorkspace` opens a new sheet for an explicit entry instead of restoring
the previously visited sheet. It waits for a successful inventory read before
mounting `PacketForm`, which preselects the ID only if that version remains ready
with a succeeded/ready generation. Missing or unavailable entries display the
source-unavailable state; the user can select another authorized source. Source
applicability still requires explicit confirmation. Existing sheets are unchanged,
and later ordinary navigation does not reuse the consumed selection.

## Response and review behavior

The sheet lists fixed sources and requirements, five outcome labels, conditions,
missing information, exact source excerpts, filenames and passage locations.
Finite retrieval and truncation are explicitly disclosed. Generation is per row;
the batch button sequences pending rows and stops at the first failure. Failed
rows require an explicit retry. Existing successful rows remain available.

Human review keeps the original model draft and adds editable text/status/details,
note, actor, time and history. Reviewed export stays disabled until every row has
a review. Download Blob URLs are revoked after use or component unmount.

`api.ts` validates HTTP responses with strict Zod schemas. In-memory operation keys
are reused after uncertain create/generate/review responses. Failed recorded
attempts receive a new key only for an explicit retry. These keys are not a browser
durable queue; after reload, recover the existing server sheet and its attempts.

For a generate network/5xx/response-parse failure, read the same sheet once without
another POST. A drafted row restores its saved result; a running row uses the existing
2.5s read polling; a failed row displays the recorded error and an explicit retry.
A still-pending row keeps its original operation key and uncertainty. Authorization
and business 4xx responses retain their normal failure path. Non-JSON errors preserve
HTTP status (`presales_http_<status>`) and a safe `X-Request-ID` fallback.
Timeout copy says that the application did not save a result while the provider may
still complete/charge, and that retry sends a new request. No synthetic draft or billing
correction is inferred from a provider dashboard. Tests must assert exactly one POST
when a 504 is followed by either a persisted draft or a persisted model timeout.

Query keys include tenant/actor/auth revision. App remounts the workspace when its
authentication context changes. Unmount aborts the operation and removes queries;
late results cannot update cache or storage. A rejected source/author/tenant
operation immediately hides old sheet contents, including when export discovers
revocation. Failed GET refreshes also hide previous data. Server authorization
remains authoritative.

Only the current sheet UUID is persisted by this module, in a tenant/actor-scoped
sessionStorage key. Requirement/response/evidence bodies stay in memory. The
explicit bearer-mode store remains available to developer clients. Default browser
mode uses the in-memory cookie/context boundary in [browser sessions](./browser-sessions.md),
including cross-tab retirement; it never falls back to a persisted bearer.

Chinese/English copy lives in `copy.ts`. CSS has a two-column desktop view and a
single-column narrow view with keyboard focus indicators and labelled controls.
No API fixture data is rendered in product or showcase mode. The dedicated
Playwright fixture clearly identifies its synthetic documents and controlled
model output; it is separate from the ordinary development/production entrypoint.

Regression files: `src/presales/PresalesWorkspace.test.tsx` (fetch boundary) and
`presales-e2e/workspace.spec.ts` (real local API/DB, controlled identity/model).
`playwright.presales.config.ts` is separate from the existing full platform E2E
configuration, and its fixture deletes only the records it created.

The separate `apps/web/playwright.presales-ingestion.config.ts` suite covers real
TXT/PDF/DOCX browser uploads, automatic ready-state refresh, source preselection,
exact excerpts and locations, review/reload/CSV, parser failure and successful
re-upload at desktop and 390px widths. Its local JWT, default database resolver,
MinIO and Redis/Celery boundaries are real; model HTTP and embeddings are controlled.
The [ingestion contract](../foundation-tests/backend/presales-ingestion.md) records
the ports, resource isolation and diagnostic-secret handling.

The [integrated first-use suite](../foundation-tests/backend/first-use.md) now
connects browser admission and invitations to the real ingestion and Presales
path, including both roles' review/CSV, usage settlement, parser/model recovery,
two-tab switching and member revocation. It uses the production API factory with
synthetic signed IdP and local model HTTP; it is not real-provider/customer acceptance.

## Proven Examples

- `apps/web/src/product/DocumentsPage.test.tsx`: polling stops at terminal states
  or read errors, explicit retry recovers, and only ready sources enable entry.
- `apps/web/src/App.presales.test.tsx`: one-use version selection takes precedence
  over a saved sheet and cannot carry across an identity change.
- `apps/web/src/presales/PresalesWorkspace.test.tsx`: asynchronous inventory
  preselection, unavailable sources, review/recovery and authorization failures.
- `apps/web/presales-ingestion-e2e/workspace.spec.ts`: real uploaded files and
  controlled responses at 1440px and 390px, with tenant denial and cleanup receipts.
