# Design: Real Provider RAG Quality Evaluation

## Boundaries

- `enterprise_doc_core.evaluation.rag_quality` owns dataset contracts, validation,
  stable-anchor resolution, deterministic scoring, and aggregate metrics.
- `scripts/evaluate_staging_rag_quality.py` owns staging orchestration, HTTP timing,
  artifact verification, report construction, CLI parsing, and process exit status.
- `evaluation/rag_quality_v1.json` and `evaluation/corpus/rag_quality_v1/*.txt` are the
  immutable synthetic input set. Changing labels or source text requires a new version.
- Existing production API and Worker behavior are not changed for evaluation convenience.

## Dataset Contract

Each document has a stable `document_key`, a repository-relative corpus path, media type,
and anchors. Each anchor has an `anchor_id`, section, optional page, and exact quoted span.
Each case has a category, one document key, query, expected outcome, expected facts,
expected anchor IDs, accepted refusal error codes, and whether it belongs in the bounded
trial sample.

Facts have a stable ID, accepted text variants, known contradictory variants, and the
anchor IDs that support them. Dataset validation resolves every reference, confines every
path under the dataset corpus root, confirms every quote occurs in its document, and
computes hashes over canonical dataset bytes plus corpus bytes.

## Runtime Mapping

The runner maps uploaded `versionId` values to `document_key` only in memory. Runtime
citations are mapped to stable anchors using:

1. the in-memory document/version mapping,
2. optional page equality when both sides provide a page,
3. optional normalized heading/section equality,
4. normalized quote containment between the golden quote and citation excerpt.

Chunk UUIDs are recorded only as one-way hashes when diagnostics require them; they are
never compared to golden labels or written raw to evidence.

## Scoring

- Fact recall: expected facts whose accepted variants occur in the answer.
- Closed-label fact precision: matched expected facts divided by matched expected plus
  known contradictory facts.
- Grounded-fact rate: matched expected facts whose supporting anchor was cited.
- Citation precision: mapped predicted anchors that are expected divided by all mapped or
  unmapped citations.
- Citation recall: expected anchors cited divided by expected anchors.
- Refusal precision/recall: computed from expected outcome and terminal run outcome.
- Refusal-reason accuracy: correct accepted error code among true expected refusals.

The report states that deterministic string labels do not replace human semantic review.

## Staging Flow

1. Validate dataset locally with zero network calls.
2. Upload each selected document through the existing multipart API and presigned object
   URL, then wait until its version appears in the authorized ready list.
3. Create one question-answer Agent run per selected case and poll to terminal status.
4. For succeeded runs, download the answer artifact, verify size and SHA-256, parse its
   strict JSON, then discard raw answer/citation bodies after scoring.
5. For refused or failed runs, retain only terminal status and public `errorCode`.
6. Build a sanitized report with per-case hashes, matched label IDs, stable anchor IDs,
   latency, model identity, and error taxonomy.

Case execution is sequential by default to respect provider concurrency and cost. A
future bounded concurrency option is outside this task.

## Evidence And Gate Policy

The first trial uses only cases marked for the bounded sample. A full 40-case run is a
separate explicit command. Provider token/cost data is `not_available` because the current
staging status/artifact API does not expose usage. No estimate is invented.

M5/M7 gate files can be updated to remove obsolete statements such as missing credentials
or missing golden set only after a successful real run. They remain open while any of the
following are absent: full representative run, repeatability, human review, usage/cost,
or other gate-specific requirements.

## Failure And Rollback

- Dataset validation failure: no network calls, no report claiming execution.
- Upload/ingestion/Agent failure: report `failed` with sanitized error code and preserve
  completed case measurements.
- Missing credentials or unreachable staging prerequisite: report `blocked_external`.
- Rollback is deletion of the new runner/module/dataset/evidence; no database migration or
  production API rollback is required.
