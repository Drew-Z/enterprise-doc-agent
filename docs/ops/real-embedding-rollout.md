# Real Embedding Rollout

This runbook replaces the enterprise staging fixture vectors with an
OpenAI-compatible `Qwen/Qwen3-Embedding-4B` route at 1024 dimensions. It does not modify the
Qdrant collection, PostgreSQL tables, aliases, or credentials currently used by the
`blog-semi` public assistant.

## Storage boundary

- `enterprise-doc-agent` continues to use its own PostgreSQL + pgvector database as the
  document, chunk, FTS, vector, generation, and citation authority.
- `blog-semi` keeps using its current public-assistant vector store during this rollout.
- The embedding provider account may be shared when its data policy and quota allow it,
  but the API key and usage budget should be independently scoped when the provider
  supports that.
- Do not point enterprise ingestion at the blog Qdrant collection or alias. A future
  shared Qdrant service must use a separate enterprise collection and alias.

## Destructive migration boundary

Migration `20260803_0010` clears existing deterministic vectors, changes
`document_chunks.embedding` from `vector(8)` to `vector(1024)`, and recreates the cosine
HNSW index. The original uploaded objects, document/version records, chunks, FTS data,
jobs, Agent runs, and blog vector data are not deleted.

Migration `20260804_0011` removes the legacy one-job-per-document-version constraint and
replaces its unique index with a normal lookup index. This migration is required before
reindexing: the tenant-scoped idempotency key still prevents duplicate requests, while a
new embedding generation can create a new ingestion job for the same document version.

Embedding generation version `2` prevents a completed version-1 Hash generation from
being reused. Existing ready enterprise documents must be reindexed after deployment.
The migration intentionally does not preserve the old 8-dimensional vector values.

## Provider contract

The reviewed default is `Qwen/Qwen3-Embedding-4B` with MRL output reduced to 1024
dimensions. `Qwen/Qwen3-Embedding-0.6B` may be used as a lower-cost fallback and
`Qwen/Qwen3-Embedding-8B` may be evaluated as a 1024-dimensional challenger. Do not
switch this pgvector HNSW schema to the 8B model's full 4096-dimensional output.

The endpoint must:

- expose an HTTPS OpenAI-compatible `/v1/embeddings` route;
- accept Bearer authentication and `{model, input, dimensions}`;
- support model `Qwen/Qwen3-Embedding-4B` or an explicitly reviewed Qwen3 equivalent;
- return one indexed 1024-dimensional finite vector per input;
- permit the selected enterprise document classification to leave the staging host.

### Free channel challenger validation (2026-08-16)

A concrete local adapter probe was run against the current Free channel without persisting its
API key. The probe embedded two enterprise-document questions through the project
`OpenAICompatibleEmbeddingProvider` and verified finite, non-zero vectors:

- Endpoint: `https://api.astrdark.cyou/v1/embeddings`.
- Requested model: `qwen3-embedding-8b`.
- Provider-reported model: `xop3qwen8bembedding`.
- Result: two vectors, each exactly 1024 dimensions.

The same channel also accepted a concrete two-document rerank request at
`/v1/rerank` with `qwen3-reranker-8b` and ranked the document containing the retention-policy
answer above an unrelated upload document.

This closes only the provider compatibility probe. It does not replace the currently reviewed
staging identity (`Qwen/Qwen3-Embedding-4B`, generation version `2`), does not prove corpus
quality, and does not constitute a staging reindex. If a later reviewed quality decision adopts
the Free challenger, set these as protected staging Environment variables:

```powershell
gh variable set STAGING_EMBEDDING_BASE_URL --env staging `
  --body 'https://api.astrdark.cyou/v1'
gh variable set STAGING_EMBEDDING_MODEL_NAME --env staging `
  --body 'qwen3-embedding-8b'
gh variable set STAGING_EMBEDDING_VERSION --env staging `
  --body '3'
```

Because the embedding model identity changes, increment `EMBEDDING__VERSION` from `2` to `3`,
re-render administrator approvals, run the embedding rollout/reindex Job, and repeat the RAG
quality gate before accepting the route. Keep the channel key only as `EMBEDDING__API_KEY` in the
operator-owned Secret; never copy it into repository files, GitHub variables, workflow inputs, or
evidence.

### Free 8B local vector evaluation (2026-08-16)

The local evaluator in `scripts/evaluate_local_embedding_candidate.py` reused the production text
parser, 1,200-character chunks with 120-character overlap, 1024-dimensional vectors, and the same
Qwen query instruction. It evaluated all 40 cases and eight synthetic documents in
`evaluation/rag_quality_v2.json` without uploading documents, writing the staging database, or
executing an Agent run. The sealed, credential-free report is
`evidence/m6/20260816-local-embedding-free8b-v3.json`; an independent repeat is recorded in
`evidence/m6/20260816-local-embedding-free8b-v3-repeat.json`.

The requested `qwen3-embedding-8b` route produced these vector-only results:

- 34 answer cases: anchor Recall@1 `0.985294`, Recall@3/5 `1.0`, and MRR `1.0`.
- Hash baseline: anchor Recall@1 `0.161765`, Recall@3 `0.852941`, and MRR `0.496078`.
- The only incomplete top-1 anchor set was `safety-retention-delete-note`; both expected anchors
  were present by rank 2.
- At the current production vector-distance boundary (`cosine >= 0.35`), four of six expected
  refusal cases still produced a vector candidate (`0.666667`).
- A diagnostic threshold scan found `cosine >= 0.55` retained complete anchor coverage for all 34
  answer cases and produced no vector candidate for the six refusal cases in this one synthetic
  run. This is calibration evidence, not an approved production threshold change.
- The repeat preserved every top-chunk order, anchor rank, Recall/MRR result, and calibration row.
  Rounded cosine values moved by at most `0.002834`; no result crossed a scanned threshold.

### Reviewed 4B local vector evaluation (2026-08-16)

The earlier Free-channel scan did not find 4B because the reviewed 4B credentials are kept in a
separate operator-owned secret, not in `grok-4.5-channel.local.env`. The staging non-secret variable
still points to `https://ai.hybgzs.com/v1`, and the local secret file identifies
`Qwen/Qwen3-Embedding-4B`. The sealed report is
`evidence/m6/20260816-local-embedding-reviewed4b-v2.json`; an independent repeat is recorded in
`evidence/m6/20260816-local-embedding-reviewed4b-v2-repeat.json`. Neither report contains the key.

Using the same 40-case corpus and retrieval contract, the reviewed 4B route produced Recall@1
`0.985294`, Recall@3/5 `1.0`, and MRR `1.0`, matching the 8B positive-retrieval results. At the
current production boundary (`cosine >= 0.35`), 4B produced a vector candidate for one of six
refusal cases (`0.166667`), versus four of six for 8B (`0.666667`). The 4B route therefore has the
better observed refusal margin; this is still a local vector-only comparison, not a new staging
rollout.

The 4B repeat preserved every top-chunk order, anchor rank, and calibration row; rounded cosine
values moved by at most `0.003658` without crossing a scanned threshold.

Decision: the 8B route is a strong retrieval-ranking challenger, but version `3` rollout remains
blocked. Keep staging on the reviewed 4B/version `2` identity; the newly confirmed 4B route is
currently the stronger candidate on the local refusal metric. Do not change protected variables or
run a reindex. The next gate is review of similarity-threshold behavior in the full hybrid retrieval
path, followed by the complete post-reindex staging RAG quality gate only if a model/version change
is approved.

The project currently has no independent reranker implementation or `RERANK__*` configuration.
The existing RRF step is deterministic candidate fusion, not cross-encoder reranking. Do not add
the tested rerank endpoint to the runtime ConfigMap until a separate retrieval slice defines its
timeout, failure fallback, candidate budget, tenant/version filtering, telemetry, and quality
evaluation contract.

Document chunks are embedded without an instruction. Query embeddings use:

```text
Instruct: Given a user question about enterprise documents, retrieve relevant passages that answer the question
Query:<user query>
```

Changing the model, revision, or output dimension requires a new embedding generation
and document reindex. Changing only the query instruction requires a retrieval evaluation
before release, but does not require document reindex because stored document vectors are
unchanged.

Configure non-secret GitHub Environment variables:

```powershell
gh variable set STAGING_EMBEDDING_BASE_URL --env staging \
  --body 'https://<embedding-provider-host>/v1'
gh variable set STAGING_EMBEDDING_MODEL_NAME --env staging \
  --body 'Qwen/Qwen3-Embedding-4B'
gh variable set STAGING_EMBEDDING_VERSION --env staging \
  --body '2'
```

Do not paste the API key into GitHub variables, repository files, issue comments, CI
inputs, or evidence. Add `EMBEDDING__API_KEY` to the existing
`enterprise-doc-secrets` Kubernetes Secret through the operator-owned secret channel.
For an interactive server session that avoids putting the value in shell history:

```bash
read -rsp 'Embedding API key: ' EMBEDDING_API_KEY && printf '\n'
export EMBEDDING_API_KEY
python3 - <<'PY' | sudo kubectl -n enterprise-doc-agent-staging patch secret \
  enterprise-doc-secrets --type merge --patch-file /dev/stdin
import json
import os

print(json.dumps({"stringData": {"EMBEDDING__API_KEY": os.environ["EMBEDDING_API_KEY"]}}))
PY
unset EMBEDDING_API_KEY
```

Verify only key presence, never its value:

```bash
sudo kubectl -n enterprise-doc-agent-staging get secret enterprise-doc-secrets \
  -o jsonpath='{.data.EMBEDDING__API_KEY}' | grep -q .
```

## Release order

1. Set both protected GitHub variables and the Kubernetes Secret key.
2. Run the normal quality workflow and publish immutable API, Worker, Consumer, and Web
   images.
3. Approve the new image digests and rendered prerequisite hash.
4. Dispatch staging. The migration Job must finish before workload rollout.
5. After the new workloads are Ready, require the restricted embedding rollout Job to
   finish before either smoke test starts.
6. Stop if migration, rollout, embedding convergence, or either smoke fails.

The staging deploy validates that the ConfigMap contains the provider, endpoint, model,
dimension `1024`, and generation version `2`; prerequisite validation also requires the
new Secret key.

## Probe and reindex

The deploy workflow runs `Job/enterprise-doc-embedding-rollout` after all workloads are
Ready and before readiness or authenticated smoke. The staging deployer cannot use
`pods/exec`; admission permits only this fixed Job name, the approved API digest, command
`enterprise-doc-embedding-rollout`, fixed bounded arguments, the runtime ServiceAccount,
and the reviewed ConfigMap and Secret references. The Pod is non-root, has a read-only
root filesystem, drops all capabilities, and does not mount a ServiceAccount token.

The CLI first embeds two fixed non-sensitive strings. It then plans at most 1,000 ready
document versions per batch, enqueues normal `document.ingest` jobs and outbox events,
and polls the same target-generation predicate while the publisher, consumer and Worker
drain the queue. Repeated apply attempts are idempotent and may report `replayed`; a batch
must always satisfy `selected == created + replayed`. The gate succeeds only when the
final plan reports `selected=0`.

The CLI deadline is 1,200 seconds, the Job deadline is 1,260 seconds, and the workflow
wait is 1,320 seconds. Provider, database, report-contract, count, or convergence failure
returns a non-zero exit. A failed or incomplete fixed-name Job is preserved for operator
review; collect its JSON report, Job object, Pod state, events and logs before explicitly
deleting it for a retry.

The final single-line report must have `status=passed`, `values_redacted=true`, a probe
with the approved provider/model, dimension `1024`, version `2`, two finite non-zero
vectors, and a completed reindex section whose final selected count is zero. The workflow
validates this JSON before smoke and adds the sanitized report and SHA-256 to the release
record. Vector values, API keys and database credentials are never report fields.

Changing the model or revision without incrementing `EMBEDDING__VERSION` is invalid: the
stable idempotency key could otherwise replay the prior generation's job. Increment the
version, render a new administrator prerequisite approval, publish immutable images, and
let the gate converge the new identity before promotion.

## Acceptance

- The embedding probe passes without exposing vector values or secrets.
- The restricted rollout Job completes within its deadline; every apply batch satisfies
  `selected == created + replayed` and the final plan reports `selected=0`.
- PostgreSQL reports `document_chunks.embedding` as `vector(1024)` and the HNSW index is
  valid.
- Every ready enterprise document has one active, succeeded, ready generation with the
  configured model, dimension `1024`, and embedding version `2`.
- A non-sensitive upload completes ingestion and hybrid retrieval returns grounded
  citations.
- Wrong-tenant and wrong-version retrieval tests still refuse access.
- The `blog-semi` public assistant continues to answer through its unchanged vector
  collection before and after the enterprise rollout.
