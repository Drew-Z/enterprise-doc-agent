# GPT question-assessment product integration

The previously successful question-assessment candidate is now available through
the real generation gateway as explicit primary/fallback opt-ins. The public draft,
review, workbook and database schemas are unchanged. Default routes keep v15/v20;
the new mode binds presales.v21 and full source/question selections to each call.

New run-v11 evaluation preserves original failures and binds every question part and
source before scoring. The existing background worker can recover from a retryable
primary502 through the separately configured legacy fallback with the original
two-request limit. It does not require a new review model or additional retries.

Schema0037 has an explicit primary-model release mode: bounded primary identity/key
and question-mode changes plus signed images, with all history-reader, idle-race,
full recovery, schema and unrelated-configuration guards retained. The staging
renderer/workflow carries both opt-in flags and rejects malformed route settings.

## Validation and current observation

- Full non-integration suite: 3,226 passed, 23 subtests; 816 integration tests excluded.
- Formatting and Ruff passed; mypy passed for 279 application source files.
- Real PostgreSQL tests verified generation, attributed review, reload, immutable
  draft, original XLSX/CSV and exactly-once successful accounting for both primary
  success and controlled502 fallback. Deployment regression suite:250 passed.
- The first real GPT product request on CQU02 received HTTP502 after24.005 seconds;
  no answer was generated and semantics cannot be assessed. The raw failure remains.
- V21 was then aligned with the earlier successful GPT candidate request:
  max_completion_tokens=4000 and omitted empty tool fields. Existing v15/v20 stay
  unchanged. The26 affected HTTP/evaluation/PostgreSQL tests passed afterward.
  The parameter difference is observed; it does not prove the cause of502.
- The corrected, bounded workflow made two calls for its first row: GPT returned502;
  the worker automatically dispatched Grok, which returned HTTP200 but stalled while
  reading the stream and hit the180-second route deadline. The row finished failed
  after210.235 seconds with providerRequestCount=2. CQU05 was not attempted. No answer
  exists to assess; this is a channel delivery failure, not another semantic rejection.
- This integration made three actual provider requests in total. All failures and
  unknown usages remain recorded. The conservative direct reservation count is45/200,
  including carried and unused reservations. No additional model requests were made.
- A separate two-row manual packet completed the real product import, attributed
  manual draft, separate assisted review, reload and original XLSX/CSV export with
  zero provider calls. The output explicitly says manual completion; it is not
  counted as GPT generation acceptance. Original drafts and review history remain
  distinct, and formulas/unselected sheets are preserved.
- Fresh postflight confirms unchanged rc49 source, schema0037, Grok primary policy,
  ready workloads, zero active jobs and unchanged application accounting. GPT has
  **not** been promoted. Signing/deploying a replacement is deferred because the
  authorized useful-generation condition has not been met, not for missing permission.

Evidence is retained under the ongoing commercial recovery group's
`fallback-model-evidence/gpt-workflow-*` and `gpt-workflow-v2-*` files. The replay
uses actual local PostgreSQL, retrieval, API, worker and remote inference with
fixture authentication/source ingestion; local hash embeddings make no external
embedding calls. It is a bounded public regression, not a live bid, customer
acceptance, independent expert review or a reliability measurement.

The completed delivery is `gpt-assisted-delivery-reviewed-20261010.xlsx`, accompanied
by `gpt-assisted-delivery-audit-20261010.csv` and the exact `*-result` snapshot. All
three owned local schemas were removed and verified after their evidence was saved.
No historical workspace files, release images, data or backups were deleted.

The concrete remaining blocker is upstream completion availability: both GPT request
forms failed with502 and the configured fallback stream timed out. These observations
cannot distinguish provider routing, model backend or network intermediary failure.
Resume with a provider-side resolution and one bounded product verification; do not
change semantic criteria, keep resampling, widen timeouts or switch models to hide it.

Recovery baseline: repository commit992ea4673bd7340f021db527dea1634196afd4a9,
phase `gpt_product_integration_20261010`, plus a verified stage snapshot preserving
the original request implementation before the compatibility correction. Staging
promotion requires useful product delivery and the existing signed-release process;
retain the current Grok configuration and compatible rc49 images for recovery.
