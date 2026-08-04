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
