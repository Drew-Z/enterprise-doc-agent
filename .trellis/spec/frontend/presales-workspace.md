# Presales workspace UI

Current route: `#/presales`, integrated with the existing App/session/navigation.
Implementation: `apps/web/src/presales`. Public API and server authorization facts
are documented in `.trellis/spec/backend/presales-workspace.md`.

The creation form uses the real document inventory, filters ready versions with
ready/succeeded generations, and asks the user to confirm source applicability.
Each pasted line is one requirement; an optional tab separates the location.
This is manual entry, not automatic splitting of a tender document.

## Execution mode UI (2026-10-08 candidate)

### 1. Scope / trigger

Expose the server's supported generation modes and the durable mode of each attempt.
This local feature does not claim that deep has passed real model quality acceptance.

### 2. Signatures

`Packet.availableExecutionModes?: ("auto" | "deep")[]` and
`Attempt.executionPolicy?: ExecutionPolicy | null`; API generate/admit and batch
methods accept an optional final `executionMode` argument.

### 3. Contracts

Render a picker only when modes are advertised. Default to the latest recorded
attempt's mode or auto; send the choice with single/batch requests and include it
in the idempotency intent. Legacy servers without the field receive legacy bodies.
Row history renders the saved mode, time budget and model names from the API.
Lost acknowledgement triggers the existing GET recovery, never an automatic POST.

### 4. Validation and errors

The strict Zod policy decoder rejects extra fields, invalid/duplicate routes,
dispatch-count mismatches, nonfinite/oversized budgets and malformed digests.
Missing legacy policy is unrecorded, not inferred. Display the explicit unavailable
policy error when the server cannot restore its accepted configuration.

### 5. Good / base / bad cases

Good: deep remains visible after refresh and offline completion. Base: no picker
for a legacy server. Bad: derive a task's mode from the current picker or automatically
resubmit generation after a lost response.

### 6. Tests and assertion points

Workspace tests assert single/batch selection and persisted-mode restoration;
API tests reject nine malformed policies. The real background browser journey
checks deep payloads, refreshed history, lost acknowledgements, offline completion,
one-time settlement and mobile layout. Ordinary review/export E2E remains separate.

### 7. Wrong versus correct

Wrong: treat a longer timeout as proof of improved quality or add internal budget
controls to the user form. Correct: offer the supported modes, explain the longer
wait, and show the strategy actually saved by the service.

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
the batch button submits multiple pending rows in one request when PacketView
generationMode is background. Exactly one eligible row uses
`POST /api/presales/{packet}/rows/{row}/generate?response=receipt`. Multiple background
rows use `POST /api/presales/{packet}/generate?response=receipt` with `{rowIds: [...]}`.
When supported, both carry `executionMode`; the legacy single-row request is bodyless.
Both return a GenerationReceipt, validated against the exact packet and complete
requested row set across admissions and rejections. Only enqueued/replayed rows
carry attemptId; already_drafted has null. The compatibility API methods without
the query option still return PacketView/BatchGenerateResult. Acceptance request gates must match the actual selected-row
count and response schema; the toolbar label alone does not identify the endpoint.
After a valid receipt, cancel older sheet reads, reset the old sheet cache and start
a new authorized GET without waiting before releasing submission busy. Show submission
confirmation while reading; no optimistic row state, draft or success is fabricated.
Clearing the old cache prevents navigation away/back from exposing a stale pending
row. A failed GET hides content and follows existing bounded/manual read recovery;
authorization/protocol failures do not poll. No automatic generation replay is added.
`api.test.ts` verifies malformed/duplicate/mismatched receipt rejection, and the
workspace tests cover delayed/failed GETs and discarded pre-admission reads. The real
background browser test delays the first GET after an actual three-row receipt,
checks navigation and hidden stale content, then completes the existing recovery/ledger flow.
The default synchronous mode retains separate row
requests, avoiding a multi-row inference request that exceeds proxy limits. Missing
generationMode from an older server defaults to synchronous. Per-row background
rejections do not stop other eligible rows. A separate retry-failed action submits
only failed rows with remaining attempts. Existing successful rows remain available.

Human review keeps the original model draft and adds editable text/status/details,
note, actor, time and history. Reviewed export stays disabled until every row has
a review. Download Blob URLs are revoked after use or component unmount.

Structured prerequisites display Met / Not met / Needs confirmation with text
labels as well as distinct colors. Each item expands only its linked exact evidence;
the complete numbered evidence list remains available. The API field is
`prerequisites: {condition, state, citationIndexes}[] | null`, with zero-based
indexes into original draft citations. Zod rejects malformed, duplicate or out-of-range
links before rendering. Missing legacy fields default to null, shown as unrecorded;
an empty list explicitly means no prerequisites. Never infer state from old prose.

`ReviewEditor` and `PrerequisiteEditor` permit text/state edits, selecting immutable
saved evidence, splitting, adding, excluding and restoring prerequisites (maximum 12).
Every effective item explicitly identifies its original draft item or Human added.
Repeated origins represent a split; unreferenced originals appear in an exclusion
list with restoration actions. The payload carries `prerequisiteChanges: {origins,
excludedIndexes}` alongside prerequisites. It cannot modify excerpt bytes or acquire
new document access. New evidence still requires the existing source workflow.

Trim edited prerequisite text before deriving conditions from unmet/unknown items;
supported cannot retain an outstanding
condition. Content/mapping changes relative to original/latest review require a note.
The server remains authoritative for range, coverage, citation, conflict and note
checks. Buttons and fields disable while saving. Empty newly added items/evidence
cannot save. Legacy null stays unrecorded unless the user explicitly starts recording;
its additions use null origins and do not invent model assessments.

Effective/original/history panels retain their separate states, links and revision
maps. Zod verifies origin count, exact original coverage, disjoint exclusions and saved
citation ranges before rendering. Older reviews without maps remain readable. CSV
includes effective/original states and the human correction record; every row still
needs review for reviewed export. Unit tests and both 1440/390px browser journeys cover
split/add/exclude, restoration, notes, disabled state, refresh and real CSV. This is
human-correction capability; model original quality is scored independently.
Give each repeated textarea/select an explicit accessible label. A wrapping label
whose text includes the control value/options is unstable in the real Chromium
label locator even when jsdom finds it. Validate current and historical mappings at
the HTTP boundary before rendering either list.

`api.ts` validates HTTP responses with strict Zod schemas. In-memory operation keys
are reused after uncertain create/generate/review responses. Failed recorded
attempts receive a new key only for an explicit retry. These keys are not a browser
durable queue; after reload, recover the existing server sheet and its attempts.

For a generate network/5xx/response-parse failure, read the same sheet once without
another POST. A drafted row restores its saved result; queued/running/recovering rows
use 2.5s read polling; a failed row displays the recorded error and an explicit retry.
A still-pending row keeps its original operation key and uncertainty. Authorization
and business 4xx responses retain their normal failure path. Non-JSON errors preserve
HTTP status (`presales_http_<status>`) and a safe `X-Request-ID` fallback.
When background mode is enabled, 202 admission releases the page busy state;
navigation or refresh recovers persisted work through GET, without another POST.
Show actual phase and generated/total counts, never invented percentages. Completion
replaces the background-running notice. Terminal failure copy retains requirements
and sources and offers later retry without exposing internal routes or exception
stacks. It does not promise zero upstream cost or fabricate a draft from a provider
dashboard. Tests assert exactly one POST when a 504 is followed by either a persisted
draft or a persisted model timeout. A lost batch response followed by completed rows
restores those results without a false whole-batch HTTP error or a second POST.
Automatic route recovery belongs to the server.

A failed sheet GET keeps an in-place **Retry reading** action next to the error,
even though the sheet heading and its ordinary refresh action are hidden. The
action only calls the current query's `refetch()` for the same sheet/context;
it cannot create a sheet or dispatch generation. It is disabled while a read is
fetching or paused. Cached bodies remain hidden during the retry and after a
denied/malformed response; only a successful authorized GET restores them.

If an uncertain generate response is followed by a failed recovery GET,
`recoverGeneration` records the sheet ID, the current query `dataUpdatedAt` and
the read error in memory. Old rows stay hidden until a later successful read.
Reconnection uses the normal Query read path; the same manual read action remains
available. A successful read clears the displayed connection failure and reports
that current row states were recovered, without inferring generation success.
Selecting the same sidebar sheet cannot clear this guard; a different sheet or
new operation retires it. Business/authorization 4xx errors keep the existing
failure path. No write is replayed, no body/operation queue is added to storage,
and a failed row still requires explicit generation retry.

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
`playwright.presales-background.config.ts` selects `presales-e2e/background.spec.ts`:
accepted batch, navigation/reload, actual browser offline completion, GET 503 and
in-place read recovery, dropped real 202 response plus failed recovery GET,
primary failure, partial completion, failed-only retry, persisted reviews/CSV,
commercial settlement, tenant isolation and 390px layout. An independent
APIRequestContext observes worker/database completion while the browser context
is offline. Two generation POSTs cover the initial batch and explicit failed-row
retry; reconnect, refresh and read retry keep five controlled provider dispatches,
three consumed reservations and one released reservation unchanged. This is a
local API/DB/worker test with a synthetic bearer and controlled model/embedding
boundaries, not live OAuth, real-provider capacity or deployed-feature evidence.
The background
spec is skipped in the legacy configuration; that skip is not a background pass.
`playwright.presales.config.ts` is separate from the existing full platform E2E
configuration, and its fixture deletes only the records it created.

For interactive deployed acceptance, reuse a user-authorized dedicated persistent
browser profile after real OAuth has been established. Keep that profile private;
do not export cookies or copy unrelated personal profiles. Waiting for the user to
sign in must not automatically close their browser or consume the generation
window. Start the bounded business window only after verifying the current tenant
and fresh server preflight. Failed checks stop automation and preserve the visible
browser; they do not authorize another generation, a new tenant, or new supplier
budget. Record separate authentication, generation, recovery and review outcomes.

Interactive OAuth need not be repeated for every business check. For an authorized
staging functional test, `issue_staging_smoke_token` can issue a short-lived JWT
for an existing active member. Verify `/api/session` before adapting the browser
authentication response; do not stub business endpoints. With `connectOverCDP`,
configure the new context's proxy explicitly: launcher options are not inherited
by the independent CDP client. Keep tokens only in memory and retire them afterward.
If reconnection already replaced the recovery button, do not wait for that vanished
button before inspecting the current result. A new browser/refresh check is separate
from proof of automatic recovery in the original window. Automated review must
identify its author and preserve the original model error, never imply customer approval.

The separate `apps/web/playwright.presales-ingestion.config.ts` suite covers real
TXT/PDF/DOCX browser uploads, automatic ready-state refresh, source preselection,
exact excerpts and locations, review/reload/CSV, parser failure and successful
re-upload at desktop and 390px widths. Its local JWT, default database resolver,
MinIO and Redis/Celery boundaries are real; model HTTP and embeddings are controlled.
The [ingestion contract](../foundation-tests/backend/presales-ingestion.md) records
the ports, resource isolation and diagnostic-secret handling.

Isolated recovery runs may publish MinIO on a random loopback port. Set
`VITE_OBJECT_STORE_ORIGINS` to that exact origin before starting Vite; the browser's
presign allowlist and the ingestion test's successful PUT counter must use the same
origin list, not port 9000. `tests.presales.browser_server` resolves one
`ApiSettings(_env_file=None)` instance and shares its database settings between the
seed engine and API. A standalone `DatabaseSettings()` ignores `DATABASE__URL` and
can seed the wrong local database. Restored-data runs must retain the selected DB
identity, explicit local endpoints, failed attempts and tenant cleanup receipts.

The [integrated first-use suite](../foundation-tests/backend/first-use.md) now
connects browser admission and invitations to the real ingestion and Presales
path, including both roles' review/CSV, usage settlement, parser/model recovery,
two-tab switching and member revocation. It uses the production API factory with
synthetic signed IdP and local model HTTP; it is not real-provider/customer acceptance.

## Proven Examples

Background generation and ordinary JSON requests use a 15-second response deadline.
An uncertain response recovers through the same sheet GET; legacy synchronous
generation retains 180 seconds. Transient GET failures can retry twice at 2.5/5-second
intervals, including when no packet loaded. Permission/protocol failures stop automatic
polling and hide cached bodies. Active work carries background guidance after remount;
submission, recovery, failed allowance and exhausted attempts have distinct copy.

- `apps/web/src/product/DocumentsPage.test.tsx`: polling stops at terminal states
  or read errors, explicit retry recovers, and only ready sources enable entry.
- `apps/web/src/App.presales.test.tsx`: one-use version selection takes precedence
  over a saved sheet and cannot carry across an identity change.
- `apps/web/src/presales/PresalesWorkspace.test.tsx`: asynchronous inventory
  preselection, unavailable sources, review/recovery and authorization failures.
- `apps/web/presales-ingestion-e2e/workspace.spec.ts`: real uploaded files and
  controlled responses at 1440px and 390px, with tenant denial and cleanup receipts.
