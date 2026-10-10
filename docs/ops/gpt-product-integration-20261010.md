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
- A separately recorded, finite two-row public-workflow replay uses the actual
  background fallback policy and at most four provider calls. Its original drafts,
  assisted review and export are separate artifacts. Publication alone does not
  establish its semantic acceptance or a completed staging switch.

Evidence is retained under the ongoing commercial recovery group's
`fallback-model-evidence/gpt-workflow-*` and `gpt-workflow-v2-*` files. The replay
uses actual local PostgreSQL, retrieval, API, worker and remote inference with
fixture authentication/source ingestion; local hash embeddings make no external
embedding calls. It is a bounded public regression, not a live bid, customer
acceptance, independent expert review or a reliability measurement.

Recovery baseline: repository commit992ea4673bd7340f021db527dea1634196afd4a9,
phase `gpt_product_integration_20261010`, plus a verified stage snapshot preserving
the original request implementation before the compatibility correction. Staging
promotion requires useful product delivery and the existing signed-release process;
retain the current Grok configuration and compatible rc49 images for recovery.
