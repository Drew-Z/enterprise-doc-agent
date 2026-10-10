# Finite v25 live regression — 2026-10-11

Four frozen first responses passed the unchanged v25 decoder. The known scope and
password-length omissions improved when the relevant public excerpts were present.
The complex service case retained its obligations. A policy fact still became a
structured business prerequisite, and the device-management conclusion still needs
wording correction. V25 is not promoted; staging remains rc51/v24.

## Frozen scope and evidence

Candidate: `f55da6a4444b5eb3ceebd70a29cfc181aeee1608`.
Prompt SHA-256: `18d3c1625c5006e123be57c3f7bc8dab8446b75d2f9c7f2b3fb7fd0301b7b67c`.
The user approved this finite observation after the local candidate checks.

The four cases were known regressions before this round, not an unseen test set:

| Case | Frozen public input | Review focus |
| --- | --- | --- |
| `secprog_allinscope` | Exact VSAQ row including background/options; GitLab ISMS lines43–50 and54–62 | Explicit scope limits, included functions and exclusions; no PCI example-driven requests |
| `application_custom_auth_pwdreq` | Exact VSAQ row; GitLab password lines8–27 and28–49 | Default8/128 limits and bans, offering/IdP boundary, no invented prerequisite |
| `office_home_device_mgmt` | Exact VSAQ row; physical-assets lines14–26 and38–53 | Documented remote-device controls, neutral uncertainty about centralized management |
| `CQU06` | Previously used question and complete archived service clause | Maintenance start/duration/fees, response versus repair, all relevant obligations and unknown supplier compliance |

The first three questions retain Google VSAQ commit
`366d670e7e2a544166f74182e1093e6162786eb7` attribution and Apache-2.0 provenance.
GitLab excerpts retain the archived URLs, UTC fetch times, hashes and CC BY-SA4.0
attribution. CQU06 is an assistant-authored question over a historical December2024
Chongqing University procurement document, not the buyer's original questionnaire
or evidence about a supplier. No new web fetch was needed.

Excerpts were manually selected before dispatch. Each is at most1800 characters;
the collector assigns diagnostic source identities. There was no live retrieval,
embedding, product persistence or release in this round. This result does not show
that real embeddings and v25 selection deliver those excerpts through the product.
It also does not isolate a prompt effect from the changed evidence versus the old
v24 product runs. The earlier controlled-vector PostgreSQL tests remain separate.

Before calling the provider, the plan bound every input and generated SourceInput,
criteria, code fingerprint, prompt, controller and deployed route policy. Review
criteria were not sent to the model. Each case received exactly one primary request:
wuye/gpt-5.6-sol, low reasoning, streaming,120-second route deadline and4000 maximum
completion tokens. There was no prompt change, output repair, resampling or fallback.

## First-result review

| Case | Original result | Assessment against the frozen criteria |
| --- | --- | --- |
| Scope | `contradicted`; one response, no prerequisite or missing-information items | Pass. States the ISMS is limited to production resources supporting GitLab.com/Dedicated, includes relevant functions and exclusions, and chooses limited scope. No PCI certification demand. |
| Password | `supported`;8/128 and documented bans retained | Requires semantic correction. The descriptive fact that GitLab enforces defaults is also emitted as a `rules` item and a `met` prerequisite. That is not a separate business precondition. No new task is requested, but the structured classification is wrong. The answer does not restate Self-Managed/Dedicated customization; it also does not incorrectly extend that feature to GitLab.com. |
| Remote devices | `insufficient_evidence`; controls described, no prerequisite | Core answer is supported; conclusion needs wording correction. Body and clarification allow “other centralized mechanisms”, so they do not strictly require a single product. The conclusion nevertheless frames the gap as absence of a unified platform/central console. Replace that with uncertainty about centralized management and coverage. |
| Service terms | `insufficient_evidence`;12 distinct obligations all `unknown` | Pass. Preserves final-acceptance start, one free year, upgrades,1-hour response,4-hour proposal/protection (not full repair),7*24 support, lifetime BUG maintenance, quality/remedies/costs and post-period market pricing. No supplier evidence was invented. |

These are two fully accepted cases, one structural semantic defect and one wording
correction; they are not a population accuracy estimate. The unedited model records
are retained. A separate signed Codex review records suggested corrections; it is
neither a regenerated model result nor an independent participant review.

Response lengths were380,270,522 and435 characters, all below the existing2000
limit. That establishes no general guarantee for longer questions. CQU06's rendered
answer is2256 characters because the renderer also includes the question, missing
information and12 obligation assessments. Verbosity remains a usability issue even
when the individual response fits the schema.

## Usage and unchanged runtime

Four generation requests:47,121 input tokens plus4,755 output tokens =51,876 known
tokens. All four returned usage; unknown usage is0 in this round. Earlier unknown
usage remains retained. Provider billing rates were not reconciled, so this is not
a monetary cost statement. Latencies:12.641,13.938,14.781 and45.844 seconds; median14.3595.
Embedding calls, retries, fallback calls and product writes:0.

Read-only checks before each call confirmed no active jobs, schema0037, the same
namespace/module fingerprints and primary v24/fallback v15 policy. Product daily
dispatches remained31; direct diagnostic requests are tracked in this central plan,
not falsely added to the product ledger. Conservative accounting reserved117 for
earlier work (including the entire previous40-call reservation), plus31 product
dispatches and4 newly authorized calls:152/200. The earlier unused allowance was
not reused. Pre/post policy and all observed product ledger counts matched.

The unchanged context/collector tests passed24 cases. Source/input/trace binding,
JSON parsing, syntax and scoped diff checks are recorded in the verification receipt.
No production code changed here; the earlier3406-unit/68-PostgreSQL results are
historical candidate checks, not rerun or relabeled as this round's results.

## Decision and next gate

Do not deploy or switch the default based on this observation. First separate
descriptive policy answers from business prerequisites and make the device conclusion
match its neutral body. Preserve these first results as regressions. A later
qualification must freeze a fresh bounded plan for actual embedding/retrieval and
product delivery; the second-supplier trial materials can support broader checks.
Independent user acceptance still requires real participants.

The concrete retry proposal is in
`docs/ops/presales-retry-policy-proposal-20261011.md`. It keeps a two-request cap,
chooses the second slot by failure type, and does not silently add a third request.
No route, timeout, retry, prompt default or quota setting changed in this round.

Recovery and full receipts are in
`D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness/fallback-model-evidence`,
prefix `v25-live-regression-20261011-`, phase `v25_live_regression_20261011`.
Existing task documents have Git recovery at the candidate commit; the two new
reports were registered absent. All96 earlier trial artifacts and2993 unrelated
Git-status entries remain unchanged. This round created only retained evidence and
report deliverables; no temporary files needed deletion. Historical worktrees,
unknown-owned files and test-framework output were left intact.
