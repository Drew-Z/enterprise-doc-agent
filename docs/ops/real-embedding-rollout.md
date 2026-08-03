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
4. Dispatch staging. The migration job must finish before workload rollout.
5. Stop if the migration, embedding probe, reindex, or authenticated smoke fails.

The staging deploy validates that the ConfigMap contains the provider, endpoint, model,
dimension `1024`, and generation version `2`; prerequisite validation also requires the
new Secret key.

## Probe and reindex

After rollout, run the provider probe inside the worker. It sends only two fixed,
non-sensitive strings and prints no vectors or credentials:

```bash
sudo kubectl -n enterprise-doc-agent-staging exec deploy/enterprise-doc-worker -- \
  enterprise-doc-embedding-probe
```

The report must show `status=passed`, `dimension=1024`, `item_count=2`, finite vectors,
non-zero norms, and `values_redacted=true`.

Plan the existing-document reindex first:

```bash
sudo kubectl -n enterprise-doc-agent-staging exec deploy/enterprise-doc-worker -- \
  enterprise-doc-reindex-embeddings --limit 1000
```

Apply only after reviewing the selected count:

```bash
sudo kubectl -n enterprise-doc-agent-staging exec deploy/enterprise-doc-worker -- \
  enterprise-doc-reindex-embeddings --apply --limit 1000
```

The command creates normal `document.ingest` jobs and outbox events. The existing
publisher and consumer process them with leases, retry limits, and heartbeat behavior.
Run the plan again after the consumer drains; `selected` must reach zero.

## Acceptance

- The embedding probe passes without exposing vector values or secrets.
- PostgreSQL reports `document_chunks.embedding` as `vector(1024)` and the HNSW index is
  valid.
- Every ready enterprise document has one active, succeeded, ready generation with the
  configured model, dimension `1024`, and embedding version `2`.
- A non-sensitive upload completes ingestion and hybrid retrieval returns grounded
  citations.
- Wrong-tenant and wrong-version retrieval tests still refuse access.
- The `blog-semi` public assistant continues to answer through its unchanged vector
  collection before and after the enterprise rollout.
