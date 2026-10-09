# Review citation correction — candidate validation

Status: implemented and locally verified; not deployed. Staging remains
`v0.1.45-rc.48`, application `6516bd0342d809f1de3a2340b2207bdc94869c75`, schema0036.

The public SWU03 replay exposed a delivery gap: a reviewer could correct the response
and add a note, but the formal citation remained the wrong source passage. The new
review editor can choose a different exact passage from the response sheet's authorized
sources. Each revision saves its own citation list, and current/original/history views
and CSV preserve those distinctions. Removing an evidence item clears its prerequisite
links; it never silently substitutes the passage that moves into the same numeric slot.
The original model/human draft and provider accounting remain intact.

This is a human correction capability. The frozen public packet was not edited or
regenerated. No actual model or embedding call, budget increase, new route or staging
write occurred during this implementation. Existing failed results and the unknown
diagnostic remain unchanged. Model reliability/semantic quality, real users, time saved,
paid demand and full competitor parity remain unproven.

## Verified behavior

- Shared literal-source search and server validation of tenant, frozen source version,
  ingestion generation and exact quote. Location metadata comes from the server.
- Ordered per-review snapshots, explicit empty selection, required correction note,
  duplicate/index/conflict validation, optimistic revision races and idempotent replay.
- Legacy request fingerprint/content compatibility. Old omission cannot silently revert
  a later explicit citation selection. Replays and exports still reauthorize access.
- Saved draft, attempts and reservations unchanged; embedding during review is forbidden
  in the focused integration test. Current CSV citations and prerequisites use the
  corrected list; original citations and human revision evidence remain separate.
- Unsaved text survives evidence browsing. Lost PUT acknowledgement triggers only GET,
  and recovery asks the user to check the returned revision. Revocation hides the editor.
- Original customer workbook delivery retains formulas and other sheets. Browser tests
  reopen exported XLSX and inspect CSV at1440 and390 pixels; human flows add zero mock
  gateway calls. The synthetic `days.` quote tests source identity, not business accuracy.

## Validation evidence

| Check | Result |
| --- | --- |
| Ruff / formatting | Passed;736 files |
| Mypy | Passed;274 source files |
| Python nonintegration | 2,967 passed;23 subtests;795 deselected |
| Affected PostgreSQL tests | 77 unique passing cases across five suites |
| Web lint / types / tests / build | Passed;457 tests; existing bundle-size advisory |
| Existing model workbook browser | 1440/390 passed with controlled fixtures |
| Human correction browser | 1440/390 passed, including lost manual/review acknowledgements |
| Visual inspection | Both final screenshots readable; no horizontal overflow |
| History-safe0037 migration | Owned-schema round trip; refuses downgrade with citation history |

Retained failures: initial unit/API red tests established the missing contract/storage;
one integration expectation used409 for source revocation and was corrected to the
existing404 contract. The first human browser run timed out on the exact label locator;
using the rendered textbox's role resolved the test at both widths. No application
retry limits or model settings were changed to make checks pass.

Local evidence lives under the existing recovery group's `fallback-model-evidence`:
`review-citations-browser-20261010` retains the first run and successful original workflow;
`review-citations-browser-v2-20261010` contains corrected human-flow screenshots, reviewed
workbooks, audit CSVs and success receipts. Both owned browser schemas were removed.

## Release boundary and recovery

Migration `20261010_0037_presales_review_citations.py` adds nullable
`presales_reviews.citations`. Null preserves the draft binding; an explicit array,
including[], is revision-specific. The old strict `content` JSON is unchanged, but
rc48 still cannot interpret the new side column correctly. Its old release guard must
continue to reject0037 until expansion, compatible-reader checks and a coordinated
release are prepared. Once citation history exists, recovery must use a compatible
reader or a forward fix; returning to rc48 would misrepresent evidence.

Recovery group:
`D:\Agent\codex\backups\tasks\20260924T015840.986Z-commercial-production-readiness`.
Phase:`review_evidence_20261010`; baseline:
`9fb1d4454cf42782f6443634ce4abf84c748476a`. Previously clean tracked files use that Git
commit as their recovery point; new files are recorded as originally absent. No extra
source-side backup copies. All2,993 unrelated worktree entries remain untouched.
No historical files, images or backup groups were deleted. Test fixtures remove only
their owned schemas and temporary resources; retained test outputs are review evidence.
