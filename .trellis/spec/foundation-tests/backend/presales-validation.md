# Offline Presales Validation Contract

## 1. Scope / Trigger

Task-local experiment at `.trellis/tasks/09-11-saas-presales-validation/`. It adds a typed CLI and HTML reference view, without changing production APIs or M5 corpus/run gates. The bundled 30 requirements are pre-segmented synthetic examples, not customer tasks or a test of requirement extraction.

## 2. Signatures

From the repository root, using Python 3.12 and existing Pydantic dependencies:

```powershell
$presalesTask = '.trellis/tasks/09-11-saas-presales-validation'
& .\.venv\Scripts\python.exe -X utf8 -B "$presalesTask/benchmark.py" prepare --output "$presalesTask/model-input.json"
& .\.venv\Scripts\python.exe -X utf8 -B "$presalesTask/benchmark.py" validate
& .\.venv\Scripts\python.exe -X utf8 -B "$presalesTask/benchmark.py" score --predictions "$presalesTask/predictions.json"
& .\.venv\Scripts\python.exe -X utf8 -B "$presalesTask/benchmark.py" render --output "$presalesTask/demo.html"
```

Common options: --corpus, --gold, --output, --force. Score additionally requires --predictions. Existing output requires explicit --force; it never permits overwriting corpus, gold, predictions, the CLI or the HTML template.

## 3. Contracts

benchmark.py owns Corpus, Gold and PredictionRun. JSON extra fields are forbidden; identifiers have a bounded token pattern, text must contain a non-whitespace character, input files are bounded at 4 MiB.

PredictionRun includes schema_version=presales-run-v1, corpus_sha256, run_id, method (human/general_ai/prototype/fixture), rows. Each row contains package_id, requirement_id, status, draft_answer, evidence, conditions, open_questions and review_state. Status is supported, conditional, contradicted, insufficient_evidence or conflicting_evidence.

A citation contains document_id/version_id/section_id/quote. Validity requires the same package, an admissible document version and an exact substring of the section. Required-reference coverage additionally requires the annotated fragment inside the quoted text.

Prepare reads only Corpus; an absent --gold path does not affect it. It emits the actual raw-byte corpus SHA-256 and a generated prediction JSON schema. Reference answers and demo.html are excluded from model input.

Score reports coverage, status matches, unsafe affirmative labels, citation validity, required evidence coverage and mechanical matches separately. It always preserves synthetic_only=true, model_quality_verified=false and human_semantic_review_required=true for this diagnostic pack.

## 4. Validation & Error Matrix

| Condition | Observable behavior |
| --- | --- |
| Missing prediction rows | Included in total denominator and missing count |
| Duplicate or unknown prediction ID | Exit 2, invalid |
| Prediction or gold corpus hash differs | Exit 2, invalid |
| Extra field / invalid enum / blank-only text | Exit 2, invalid, field/type location without raw document values |
| Forged, obsolete or cross-package citation | Invalid citation, no required-evidence credit |
| Conflict only cites one required side | Incomplete coverage |
| Conditional label lacks conditions | Missing-condition metric; no mechanical match |
| No citations | citation_validity is null |
| Existing output without --force | Exit 2, output preserved |
| Output aliases input/tool even with --force | Exit 2, output preserved |
| Successfully scored an incorrect run | Exit 0 means computation succeeded, not quality accepted |

## 5. Good / Base / Bad Cases

Good: P1-R01 already includes enterprise edition, so SAML evidence supports the offer. Base: P1-R03 is conditional because private deployment exists but its add-on is not selected. Bad: an unconditional supported label for P1-R03 counts as unsafe; evidence being real does not remove that condition.

P1-R04 needs both daily and weekly backup documents. The obsolete 7-year retention document cannot overrule the current 3-year cap. Text resembling instructions or HTML remains inert document content.

## 6. Tests Required

Run test_presales_benchmark.py through its CLI boundary. Verify answer isolation, partial results, unsafe affirmatives, forged/old/cross-package sources, unknown/duplicate identifiers, hash mismatch and output/source protection.

Run test_demo.mjs with the repository's existing Playwright. Verify filtering, source viewing, edit -> unreviewed transition, provenance-preserving JSON export, mobile overflow and no executable source text or network requests. Its temporary browser context closes after testing.

Use temporary real directories for mypy cache on Windows; the reserved NUL path caused an internal error in the observed mypy runtime. The temporary directory must be automatically cleaned.

## 7. Wrong vs Correct

Wrong: divide matches by returned rows, call fixture replay 100% model accuracy, or feed gold/demo files into the model.

Correct: retain all expected requirements in the denominator, mark fixture replay explicitly, supply only prepared inputs to an independent model context, and separately review free-text meaning, condition completeness, actual time saved, repeat use and payment.
