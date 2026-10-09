# Excel questionnaire workflow

User authorization: implement the previously proposed bounded Excel workflow (2026-10-09). Reuse canonical checkout and the commercial task recovery group; no deployment or new provider acceptance claim.

Flow: authenticated bounded JSON upload -> XLSX inspection -> user confirms one visible worksheet, question/answer columns and row range -> server reparses identical SHA256 content -> atomic packet, requirements, mapping and original-byte persistence -> existing per-row generation/review -> authorized original-workbook export.

Use openpyxl for reading Excel values/coordinates and lxml for a narrow OOXML worksheet edit. Do not save through a workbook reserializer: copy all ZIP members except the selected worksheet verbatim, and retain all unrelated nodes/styles there. This is application runtime code, not an authored spreadsheet artifact. Answer values are inline strings, never formulas. No formula evaluation, macro execution, external URL fetch or provider calls during import/export.

Boundaries: ordinary transitional XLSX, 2 MiB compressed, 20 MiB expanded, 256 ZIP members, 20 worksheets, 50,000 physical cells, rows <=10,000 and columns <=256. At most 120 nonblank questions in an explicitly selected range. Reject encrypted/macro/signed/embedded/external-link packages and unsafe XML/ZIP; reject protected/hidden selected sheets, hidden selected questions, merged/formula questions or answer targets, populated targets, table/validation targets and oversized text. Return stable safe errors, no file bodies in logs. Preview is ephemeral and requires membership authorization. Creation checks existing demo limits and a separate 20 MiB original-workbook tenant storage cap under the existing tenant lock. This attachment storage is bounded PostgreSQL bytea, retained/deleted with its packet/tenant, not a document ingestion or embedding.

Add nullable workbook metadata and deferred bytea on PresalesPacket in additive migration 0035. Manual packets remain <=12 rows. Imported packets use an internal validated create payload <=120; batch admission remains <=12. Fingerprint includes original SHA256 and confirmed mapping; same-key replay rechecks authorization and returns the same packet. Packet metadata contains filename, SHA256, selected mapping, ordered Excel rows. Never include bytes in packet GET. Downgrade refuses if any workbook exists; old release readers may not support imported packets, so deploy API/frontend together and retain schema on application rollback.

Frontend shares title/source selection with manual entry. File lives only in component memory, discarded with form/auth-context unmount. Mapping changes invalidate confirmation. Display exact question/answer cells before creation. Reopen uses durable metadata. Paginate rows in groups of 12; generation buttons submit only the next <=12 pending/failed rows, without automatic replay or quota increases. XLSX export and existing audit CSV remain distinct controls. Draft workbook marks unreviewed/unavailable rows; reviewed export requires all rows reviewed. Both include conditions and missing information without silent truncation; detailed evidence/review history remains in existing CSV and app.

Mock only provider/network boundaries. Test genuine ZIP/XLSX parsing, persistence and real PostgreSQL constraints in owned loopback schemas; browser assertions cover upload, mapping confirmation, reload, batch boundaries and download. No public schema migration.

## Approved release continuation

Reuse the existing supervised expansion executor with a second fixed migration, 0034-to-0035. Plans select only a known original/target pair and the exact built-in SQL digest. The same credential-private psql session, advisory lock, deadlines and resource fences apply; compare complete column/check shapes and recover by observing the committed state, never by replaying or dropping columns.

On 0035, an image-only plan explicitly binds whether original and candidate images support workbook records. A legacy side requires an empty-workbook read before writes and again after admission closes. Check both deployment and recovery sides before apply, and the original side before restore. Preserve all metadata/content and the expanded schema. rc.45 may be rolled back to only before workbook import; after history exists, use a compatible candidate or a forward fix, never erase history to make rollback possible.

Freeze the public source, original file hash, selected range, source applicability, call budget and review criteria before product replay. Keep this file-flow exercise distinct from the previously completed six-call generation comparison and failed commercial acceptance.

## Focused public replay remediation

Read-only observation confirms PostgreSQL simple full text treats the unspaced SWU03 query as one whole token; neither primary nor OR fallback matches any of17 chunks, including two containing the required clause. Add a bounded character n-gram fallback within the existing authorized document query when primary full-text recall is empty. Keep one keyword query roundtrip and existing vector calls/RRF/top-k; do not infer semantic truth from lexical matching.

Output failure details were not persisted, so the historical invalid outputs cannot be reconstructed. Add allowlisted diagnostic categories to PresalesError and existing attempt provenance (per dispatch number for background, one field for synchronous), never raw content. Keep public error codes, outage/recovery classification, usage and deadlines unchanged. Do not claim this diagnostic patch alone fixes historical model failures.

Prompt v15 keeps the v14 literal-basis schema/decoder and adds scope/negative-evidence guidance plus relevant-citation selection. It cannot deterministically prove entailment. Old decoder reports and saved attempts remain unchanged; new admission freezes the new prompt SHA. A deployment must drain old accepted policies before switching.

## Human response persistence and compatibility

Keep SavedDraft JSON unchanged as the immutable response baseline; add nullable row manual_authorship JSONB in additive migration0036 with server actor/time/note and private operation-key/fingerprint. Public RowView exposes only actor/time/note. Null retains historical model attribution. The same row.draft fence protects against generation overwrite and existing review/export rules are reused. Manual creation writes revision1, no attempt/review success is fabricated; review remains a separate explicit action. Existing drafts cannot be replaced. All raw active attempt states reject, including expired-but-unreconciled executions; no implicit cancellation or settlement.

Manual evidence browse is a reauthorized, bounded literal substring search within packet snapshot versions/generations,10 chunks per page with600-character snippets. No retriever/provider is called. Save resolves every chosen chunk and exact substring against the current authorized generation and reconstructs filename/location server-side, then applies existing response/citation/prerequisite rules. Tenant/packet/row lock ordering serializes competing manual/generation writes; same-key replay precedes revision checks and always reauthorizes.

Migration downgrade locks rows and refuses any manual history. Older rc47/rc46 JSON readers can parse the unchanged draft bytes but misattribute human content; they are therefore NOT compatible rollback targets once manual history exists. Do not deploy0036 or write staging manual records until coordinated API/Worker/Web release and explicit manual-reader rollback guards are implemented and verified. Existing0035 release tooling fails closed on0036. The initial phase validated local product behavior; the authorized release continuation below subsequently satisfied those deployment guards. Preserve frozen public packets.

## Authorized manual release continuation (2026-10-10)

Extend the fixed executor with exactly0035-to-0036, retaining all original images/configuration. Validate the complete0034/0035 column/check shape plus the nullable, default-free manual_authorship JSONB and its exact validated check. Share the existing advisory lock, private psql session, timeout/readback and atomic transaction. The expansion plan binds unchanged workbook-reader capabilities; recovery observes either complete revision without replay or downgrade. Expansion recovery must refuse original applications if manual history has appeared, both before closing and before reopening.

On0036, image-only plans require separate exact boolean original/candidate workbook_readers and manual_readers. Apply checks both deployment and recovery readers; restore checks the original reader. For each incompatible capability, require empty corresponding history before writes and after all applications stop. A racing submission keeps applications stopped and cannot change image/configuration/credential bindings. Existing workbook history must remain readable throughout. No configuration, provider route, retry or budget changes.

Validate through ReleasePlan/ReleaseCluster and PsqlSession boundaries: controlled Kubernetes I/O and clock, actual Alembic SQL and owned PostgreSQL schemas. Cover migration rollback/lost receipts/shape drift, both history races, compatible recovery with real human responses, and immutable workbook/human metadata. Then publish exact signed images, expand, release, exercise rc47 rollback only before manual history, and reapply. New owned manual-only live fixtures verify evidence -> save -> review -> export without provider calls. Preserve the frozen public packet and all failed model results. After human history exists, recovery requires compatible images or forward repair.

Executed: rc48/0036 passed expansion, release/rc47 rollback/reapply, followed by one new
human-only workbook and the actual history-aware refusal through a read-only Kubernetes
boundary. Human records now exist, so the pre-history rc47 drill is not an available
current rollback. Input workbook creation belongs outside the read-only application
container; the product imports existing bytes and exports through its narrow OOXML path.
The corrected acceptance harness follows that boundary without relaxing runtime mounts.

## Review evidence correction

ReviewInput adds nullable citations (<=12 unique CitationInput values). Omission/null
retains the historical original-draft contract and fingerprint. Once the latest review
has its own citations, a new review must explicitly supply its evidence; an old request
cannot silently revert it. Existing-key replay remains valid and reauthorized.

Resolve explicit citations against the packet's tenant/version/active-generation and
literal text, using the same bounded resolver as manual entry. Derive locations on the
server. Validate status, conflict and prerequisite indexes against this exact ordered
set. Changing evidence or prior assumptions requires a note. Preserve the tenant/row
transaction and final authorization checks; no generation/accounting operation occurs.

Add nullable JSONB presales_reviews.citations in0037. SavedReview exposes the full
server-resolved evidence snapshot; keep historical content JSON unchanged and merge
the side column in the existing MVCC history query. Each revision's indexes refer to
its own evidence; null means immutable draft evidence. Downgrade locks and refuses any
non-null citation history. rc48 cannot interpret new citations, so no staging writes
until a coordinated release and reader guard is verified; existing0036 tooling rejects0037.

Extract the existing literal evidence selector for reuse by manual creation and review.
On selection changes remap retained prerequisite references by exact evidence identity;
removed links become visibly unselected and block save until the reviewer resolves them.
Show effective, original and historical evidence separately, and append original-evidence
and correction provenance to CSV. Do not reset the response form when browsing evidence.

## Review citation release continuation

Reuse the supervised fixed expansion executor with exactly0036-to-0037 and the same
private psql session/advisory lock/deadline. Retain all original images and configuration,
and bind unchanged workbook/manual reader capabilities. Verify inherited schema plus
nullable/default-free citations JSONB and its exact validated array/length constraint.
Recovery reconciles complete0036/0037 without replay or downgrade; any nonnull citation
history on0037 prevents reopening original0036 readers before writes and before reopening.

Image-only0037 plans require independent boolean workbook/manual/citation reader
capabilities for original and candidate. Apply checks both sides, restore original;
each incompatible history is checked before writes and after applications stop.
Preserve existing human/workbook history, credentials, provider settings and all failed
sample results. Validate controlled cluster races and actual owned PostgreSQL migration,
unknown commit receipts, schema drift, real corrected review/export and refusal with[].
Publish and validate exact source before signed release. A live rc48 rollback drill may
occur only before new citation history; afterwards use compatible images/forward repair.

Executed: rc49 / 0037 passed the schema window and release/rc48 rollback/reapply before
independent citation history. A new human-only fixture then verified two separate review
snapshots, original draft/authorship, corrected export and unchanged accounting. Actual
restoration now refuses rc48 before writes. Capacity stayed under the existing complete-batch
guard after the specifically approved rc36–37 cleanup; no reserve or import-contract change.

Live fixture identity binds persisted imported key X7, exact question and B7/C7 location;
SWU03 is a public replay label, not the application's imported row key. The first harness
failed before packet creation, was diagnosed read-only and retained. No product or model
behavior changed during that correction. Keep historical samples immutable.

## Generation diagnostic evidence

The collector currently catches PresalesError but saves only errorCode, discarding the
allowlisted diagnostic_code already produced by the real gateway. Add optional
errorDiagnostic only when present; retain the existing v6 decoder, traces and failed
state. Do not store exception strings or infer a category when the gateway has none.
Controlled HTTP proves retention, safe fields and unchanged no-retry behavior before
any fresh public-source diagnostic. Keep live input and separately frozen review criteria
outside the collector; existing source/hash validation and exclusive output creation apply.

## Coverage candidate

Keep the v14/v15 BasisDraft field names, bounds, validators and projection unchanged.
Describe proposition as a concrete business event/capability to assess; definition alone
contains the rule establishing necessity. Explicitly distinguish known rules from unknown
current facts. Describe answer as the response to every requested aspect, and require
specific missing arrangements rather than an invented plan or a generic proof request.
Use prompt v16 so new execution policies bind the changed system/schema description SHA.

This is a model-instruction change, not deterministic semantic enforcement. The original
CQU03 outcome is the observed failing baseline; controlled schema tests cannot prove the
model now understands it. Preserve historical decoders and score bytes. Before one fresh
CQU01 request, freeze the candidate and separate coverage/event criteria; retain failures
and reject broad quality claims. Staging remains rc49/v15 until an exact compatible release.
