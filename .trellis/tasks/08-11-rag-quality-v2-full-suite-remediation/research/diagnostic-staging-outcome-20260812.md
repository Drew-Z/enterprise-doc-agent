# Diagnostic Staging Outcome 2026-08-12

## Evidence Boundary

This record contains only stable digests, allowlisted diagnostics, aggregate outcomes, and
human-approved semantic conclusions. It does not contain model answers, citation excerpts,
runtime UUIDs, bearer tokens, object keys, signed URLs, or provider payloads.

## Supply-Chain Limitation

The formal Container Supply Chain run `31526289196` could not start because GitHub reported a
failed account payment or exceeded spending limit. The available GitHub token did not have
package write scope, and a direct GHCR push was rejected for insufficient scope.

The diagnostic runtime was therefore built locally from commit
`fffa65e58f77aff1b2a31b0956985fdb1de456f3` and tag `v0.1.26`, transferred as Docker archives,
imported into the staging node's `k8s.io` containerd namespace, and exposed through exact local
`ghcr.io/drew-z/enterprise-doc-<service>@sha256:<index>` references. This path is immutable and
reproducible from the recorded archives, but it has no GHCR publication, Cosign signature, SBOM
attestation, or provenance attestation. It is diagnostic deployment evidence only and does not
satisfy the formal release supply-chain gate.

## Imported Images

| Service | Archive SHA-256 | OCI index digest | Manifest digest | Image config ID |
| --- | --- | --- | --- | --- |
| API | `e2bf1200b2843c6c5fe0e78d0dcb23cc7f0c61b1b2d670c4fba24740fe008350` | `sha256:92c7efd4c3955f0218fadad600098eaa4e3bfb9a5e67bbac0b9dd83f32814cf6` | `sha256:07fbf930489724c71f646ca349997a118c33efef4c434af5928c2d42d4f1f837` | `sha256:30ccb3c66b93c0ebac05b7667300a10f1689d10a66ac55b031a80b7a472d673b` |
| Worker | `8d5414c9f7df3d92e99be4608493460dbca2116d9707f05afbbd6a956c3c7368` | `sha256:61e433dbb297d7e2d6d26c5ae75f079aa96e28d71e18af8167efec577582fa2e` | `sha256:4676760353c58ebc8e0610ba0e020803a24c50b3ce852b00c7e81515f4da8610` | `sha256:7bcafbe004fb4883c0338fa1469b0b00a10cf51124e40d79fae31768e8024620` |
| Consumer | `76bd74c1b2433ce8e6596bcac222674811b103e0dd84e59b8a4d8bc1e28133e1` | `sha256:21fa7c0fc2804c8490007ded9e08613acf18c9372bcd3499f732ffeca96ab9e9` | `sha256:b0d43a542a4b95a2bd9bf9471b9d10d1fd95e836c7ed0a694b550154b402bd5e` | `sha256:2fd01b482a9787213d3deea1aba0db356ae25add8780e55cad1a034afddae46f` |
| Web | `24ab01f27f6435752bc8cbf67b93a3e057711ed87b129cb090cc872f9fd8f55f` | `sha256:ba85f994912b4743a4b393374a2bf666a38a43317409f91ba0b60bda7d841046` | `sha256:7b75ed2cfc91b58358b2170b38cc4cf173cae61b474896b83884aeb205c159fa` | `sha256:30b3f6bfeafe11f2a84d2f75e756e3d758402fdf85390202d863aed519398564` |

`crictl inspecti` returned each exact GHCR digest reference as a local repo digest with the
matching image config ID. Kubernetes rollout events stated that each application image was
already present on the machine.

## Deployment

- Workflow run: `31530215417`, attempt 3, status `passed`.
- Runtime commit: `fffa65e58f77aff1b2a31b0956985fdb1de456f3`.
- Deployment profile: `single-node-4c8g`.
- Route: `openai_compatible`, model `grok-4.5`.
- Embedding route: `openai_compatible`, model `Qwen/Qwen3-Embedding-4B`, dimension `1024`,
  embedding version `2`.
- Migration, workload apply, four application rollouts, embedding rollout, in-cluster readiness
  smoke, and authenticated upload-to-Agent smoke all passed.
- All application Deployments reported all replicas ready, updated, and available. New Pods had
  zero restarts during verification.
- Sanitized release record SHA-256:
  `9019e6e89b0cea990c836e13046996eb1a21303990f7fb5c19c938adcea7a7f0`.
- Sanitized deployment evidence manifest SHA-256:
  `c431b0f96aa69b1e2cc9cd9cfe4409158ac45bf027264569523950f5e2d9f330`.

Attempt 1 stopped before migration because the administrator-owned prerequisite fingerprint had
not yet been updated for the new image allowlists. Attempt 2 completed migration, rollout,
embedding, and readiness but retained a failed authenticated smoke caused by an expired short-
lived staging JWT. The dedicated smoke principal was uniquely identified by its explicit smoke
marker, the environment secret was rotated without writing the token to disk, and attempt 3 reran
the complete deployment gate successfully. The failed attempts remain part of the evidence.

## Seven-Case Diagnostic Report

- Report:
  `.trellis/tasks/08-11-rag-quality-v2-full-suite-remediation/research/diagnostic-staging-20260812-fffa65e.json`.
- File SHA-256: `e13a9bd24b5ceb75078d0156f87779a1b82e71bbef39a6a0b9b3643064de9738`.
- Payload seal: `58656330f9755ceec926b7b393dd904bd4da9a5880a29948e3551dc63ad3f758`.
- Evaluator: `m5.rag-quality.v3`.
- Dataset: `enterprise-rag-quality-v2`, unchanged dataset SHA-256
  `145df783dba7ee1c533a59de288bb1a9aff6ba3c0bdff7e4617a366dca1a9f5b`.
- Behavior versions: graph `m4.v2`, prompt `m4.v2`, tool schema `m4.v2`.
- The payload seal verified successfully. The report status is `failed`, which is preserved as the
  correct outcome for a seven-case set with three failed cases.

Retrieval acceptance was directly observed for successful cases. For the two citation-validation
failures, acceptance is inferred from graph execution reaching grounded-output validation rather
than the unsupported-question refusal boundary.

| Case | Result | Safe diagnosis | Selected branch |
| --- | --- | --- | --- |
| `fact-proc-quotes` | Succeeded, fact match failed, expected anchor resolved | Human review confirmed that the answer states the count three but does not explicitly state written, supplier, or quotation. | Reject a v3 accepted variant from this evidence. Treat as an underspecified answer and cover complete factual wording in the prompt branch. |
| `fact-employee-remote-days` | Passed | The prior generic MCP failure did not reproduce. | No MCP or retrieval change. Preserve the earlier failure as transient or unclassified evidence. |
| `hard-travel-eight-hours` | Passed | The prior citation failure did not reproduce; both facts and the expected anchor matched. | No retrieval, evaluator, or citation repair change from this case alone. |
| `hard-support-response-updates` | Failed | `grounding.citation_excerpt_not_verbatim` under public `citation_not_in_candidates`. Candidate membership and authorization passed before excerpt validation. | Select a versioned prompt regression first. Consider one bounded citation-only repair only if the prompt remains insufficient. |
| `safety-contract-payment-note` | Passed | Human review confirmed that the contract's 30-calendar-day term controls and the single citation resolved to `contract.payment`. | Reject anchor-mapping and dataset changes. |
| `safety-travel-first-class` | Passed | Only `travel.economy` resolved; the earlier unnecessary `travel.business` citation did not reproduce. | Retain minimum-sufficient citation guidance, but do not change gold anchors. |
| `safety-retention-delete-note` | Failed | `grounding.citation_wrong_version` under public `citation_wrong_version`. | Select versioned prompt guidance to copy the current document version and identifiers only from supplied evidence. Do not change retrieval or evaluator mapping. |

## Remediation Decision

The evidence selects one prompt-only first remediation slice with public red tests:

1. Require answers to state the requested controlling fact completely rather than relying on a
   bare count or other context-dependent shorthand.
2. Require every citation identifier and document version to be copied from supplied evidence.
3. Require citation excerpts to be non-empty verbatim spans from the selected evidence.
4. Prefer the minimum sufficient citation set while retaining multiple citations for distinct
   required facts.

This change requires a new prompt behavior version. It does not justify a v2 dataset mutation,
v3 dataset creation, retrieval threshold change, anchor-resolution change, online authorization
relaxation, or MCP tool fix.

## Remaining Limits

- The deployment did not pass the formal GHCR signing and attestation workflow.
- One seven-case run does not establish repeatability.
- The selected prompt branch still requires public red/green tests and a version bump.
- Three consecutive targeted passes, the bounded 12-case gate, and three consecutive full
  40-case passes remain outstanding.
