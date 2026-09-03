# Blog RAG 迁移与公开 Agentic RAG 整合方案

日期：2026-07-23

本文基于当前代码审计，记录 `blog-semi`、`chatus` 与
`enterprise-doc-agent` 的真实边界及迁移决策。文中不记录 API Key、Token、
私有端点、文档正文或个人身份信息。

## 1. 已澄清的目标

这里的“外部助手”专指 `blog-semi` 页面上的匿名公开助手，不是外部 Codex
内容生产 Skill。

最终目标分为三件事：

1. 将 `blog-semi` 当前拥有的公开知识数据、同步流程、embedding、Qdrant、
   pgvector、混合检索、引用和质量评估能力迁移到
   `enterprise-doc-agent`，由后者统一拥有和运行。
2. 将公开助手从当前“一次检索 + 一次生成”的 Native RAG 升级为真正的
   只读 Agentic RAG，但不授予 memory、Studio 写入、项目管理、状态管理、
   draft 或 publish 等内部能力。
3. 精简 `blog-semi`：保留博客页面、公开助手 UI 和一个轻量同源网关；迁移
   稳定后删除站内 Operator Agent、RAG 服务、向量库密钥和重复模型链路。

`chatus` 继续作为独立的私有聊天/Agent 产品。当前代码中没有 blog RAG、
embedding、Qdrant 或 pgvector 实现，不需要为本次迁移修改它。

## 2. 关键概念澄清

### 2.1 “复用 embedding 客户端设计”指什么

它只指把文本可靠地转换为向量的底层 provider 能力，包括：

- 调用 OpenAI-compatible `/embeddings`；
- 批量发送并保持返回顺序；
- timeout、取消、429/5xx 重试和 `Retry-After`；
- 大批次失败时递归拆分；
- 数值、数量和向量维度校验；
- ingestion 与 query 共用相同的 model、revision 和 dimension。

代码参考是 `blog-semi/server/src/ragEmbeddings.ts`。目标项目应使用 Python
重新实现 `OpenAICompatibleEmbeddingProvider`，而不是直接依赖 Node 模块。

这不是公开助手的 RAG 链路。embedding client 只负责“向量怎么生成”，并不
负责 query rewrite、召回、rerank、证据判断、答案生成或引用校验。

### 2.2 “复用整个 blog RAG”指什么

本方案中的“整个”是能力和数据所有权的迁移，包括：

- 公开知识原始数据与可回滚快照；
- chunk、source URI、标题、标签、实体和关系等元数据；
- embedding provider 和批处理行为；
- PostgreSQL FTS + pgvector 检索；
- Qdrant vector backend adapter、collection 导出和恢复能力；
- sync/reindex/health/smoke/evaluation 运维流程；
- citation、拒答、可观测性和回滚证据。

它不表示将 blog 的旧表、旧 payload 和旧权限边界原样复制进企业私有表，
也不表示让 Qdrant 与 pgvector 永久执行无约束双写、双查。

## 3. 当前代码的真实情况

### 3.1 公开助手不是 Agentic RAG

公开浏览器组件匿名调用 `/chat/public`，服务端先调用一次 RAG，再调用一次
模型生成答案。当前没有以下闭环：

- 查询分类与多子问题分解；
- 模型驱动的 query rewrite；
- 基于证据缺口的二次检索；
- 多跳检索规划；
- 证据充分性反思；
- claim-to-citation 覆盖校验。

因此，当前路径属于带混合召回的单轮 RAG，而不是完整 Agentic RAG。

### 3.2 `agentic-hybrid-*` 名称高于实际能力

`ragOrchestrator.ts:34-50` 的行为是按配置选择 Qdrant、PostgreSQL 或本地
检索器，然后只执行一次召回。

Qdrant 路线在 `ragQdrantStore.ts:140-181` 中只对原始 query 做一次
embedding 和 vector search，再按已有 score 合并、截断。虽然 metadata
写了 `reranked: true` 和 `agentic-hybrid-qdrant`，但代码没有真实 reranker、
query decomposition 或检索迭代。

pgvector 路线更完整：`ragPostgresStore.ts:94-142` 并行执行 keyword、vector
和 entity recall 后合并候选。但它仍然只是一次固定检索，不是 Agentic
检索循环，也没有独立 cross-encoder/LLM reranker。

迁移时应保留这些有效能力，但不要沿用会误导面试和运维判断的命名。

### 3.3 旧 Operator 图不能直接公开

旧 Operator Agent 具备项目/status/knowledge 查询、Studio draft、memory 等
工具。匿名公开助手不需要这些能力。将完整 Operator LangGraph 或完整 MCP
工具面直接暴露给公开入口，会放大越权、误配置和调用成本风险。

### 3.4 企业项目现有边界

`enterprise-doc-agent` 目前以
`tenant_id + document_version_id + active generation` 作为核心边界：

- keyword 与 vector SQL 都硬过滤 tenant、单一 document version 和可用
  generation；
- execution context 绑定 tenant、actor 和 target version；
- evidence freeze 与 citation gate 会拒绝跨 tenant/version/chunk 的引用；
- PostgreSQL 同时保存 FTS、pgvector、来源、offset 与内容 hash；
- 当前还没有 Qdrant Python client 或 corpus 一等实体。

这套边界应继续保护企业私有数据，不能为了迁入博客公共数据而弱化。

## 4. 目标架构

```text
Anonymous browser
  -> blog-semi /api/chat/public
     - same-origin facade
     - input limit, rate limit, budget, total deadline
     - no model/RAG/database secret in browser
  -> enterprise-doc-agent Public Assistant Facade
     - service authentication
     - server-fixed public tenant and active corpus snapshot
     - read-only execution context
  -> Public Agentic RAG Graph
     - query analysis and bounded retrieval loop
     - frozen evidence and deterministic citation gate
     - Responses API grounded generation
  -> answer + public citations

enterprise-doc-agent
  enterprise_private
    - existing tenant/version/generation PostgreSQL + pgvector
  blog_public
    - isolated public tenant and versioned corpus snapshot
    - PostgreSQL as metadata/FTS/generation authority
    - configurable vector backend: pgvector OR Qdrant
```

`blog-semi` 最终只保留：

- 静态博客和内容数据；
- 公开助手 UI；
- 同源 public chat facade；
- Studio/AI Daily 仅在确认仍需要时独立保留。

它不再拥有：

- Operator Agent 与内部工具；
- RAG orchestrator；
- Qdrant/pgvector/embedding/model 密钥；
- RAG sync、reindex、health 和 eval 作业。

## 5. Public 数据隔离设计

### 5.1 第一阶段的最小兼容方案

建立固定的 public tenant、system actor 和 active membership。每次博客知识
发布生成一个不可变的“公共知识快照”，将其作为一个逻辑 Document 的新
DocumentVersion 与 ingestion generation。

每个 chunk 仍保留原页面的 title、canonical URI、section、content hash、
tags 和原始 source id。服务端配置只指向一个 ready 的 active public
version；浏览器不得提交 tenant、actor、document version、corpus 或
capability。

优点：不破坏现有单 version execution context、evidence freeze 和 citation
gate，可以最快完成安全迁移和公开灰度。

### 5.2 何时升级为 `CorpusRelease`

若后续必须让多个独立 Document 保持各自生命周期，又要在一次 run 中跨
文档检索，应新增一等 `Corpus`/`CorpusRelease`/member 实体，而不是只加一个
不参与授权的 `domain` 字符串。

`CorpusRelease` 必须冻结成员 version 与 generation；execution context、
retrieval、evidence、citation 和审计都绑定 release id。这需要 context v2
和数据库迁移，不应在第一阶段草率实现。

## 6. Qdrant 与 pgvector 的处理方式

用户要求迁移整个 Qdrant/pgvector 能力是可行的，但需要区分“迁走能力”与
“同时作为权威数据源”。

推荐约束：

1. PostgreSQL 始终是 document/chunk/source/generation/citation 的权威库，
   同时承担 keyword/FTS。
2. 配置 `VECTOR_BACKEND=pgvector|qdrant`，一个 corpus snapshot 在一次线上
   查询中只选一个 vector backend。
3. 选择 pgvector 时，keyword 与 vector 都在同一 generation 内完成，作为
   第一版默认方案。
4. 选择 Qdrant 时，Qdrant 只保存带 snapshot/generation/model revision/
   dimension/content hash 的向量索引；PostgreSQL 继续保存权威文本和引用
   元数据。
5. 新 snapshot 只有在 PostgreSQL 写入、embedding、vector indexing、数量
   与 hash 校验全部成功后才允许原子激活。
6. 不同时查询 Qdrant vector 与 pgvector vector 后简单拼接；否则会产生
   重复候选、分数不可比以及部分同步时的版本漂移。

迁移时应先：

- 导出原 Qdrant collections、point payload、collection dimension 和数量；
- 导出原 pgvector schema、记录数、模型信息和数据校验 hash；
- 从公开知识源重新构建一个新 snapshot；
- 对旧库与新库执行抽样 citation/hash 对账；
- 在回滚观察期保留旧 collection 和数据库只读备份。

因此，“整个迁走”会保留 Qdrant adapter、导出/恢复和基准能力，但第一版不
为了表面上的“双向量库”制造长期双写一致性问题。只有真实压测证明 Qdrant
在目标数据规模、延迟或过滤能力上明显胜出，才将 public corpus 的
`VECTOR_BACKEND` 切换为 Qdrant。

## 7. 公开只读 Agentic RAG 图

推荐使用受信任的服务端状态图，而不是把完整 MCP 工具列表直接交给上游
模型。Responses API 在第一版只承担结构化分析和 grounded generation。

```text
validate_public_request
  -> classify_and_decompose
  -> rewrite_subqueries
  -> parallel_retrieve
       PostgreSQL FTS
       selected vector backend
       entity/relation expansion when available
  -> fuse_deduplicate_and_rerank
  -> assess_evidence_sufficiency
       enough -> freeze_evidence
       weak   -> one bounded gap-directed retrieval iteration
       none   -> deterministic refusal
  -> grounded_generate_with_responses
  -> schema_and_citation_gate
       pass   -> public answer
       fail   -> one bounded repair or refusal
```

公开图只注册或直接调用以下只读能力：

- `search_public_evidence`；
- `read_public_chunk`；
- 可选的 `expand_public_entities`。

不注册 memory、Studio、draft、artifact、publish、project admin、status admin
或任意 enterprise tenant 选择能力。public scope 由服务端固定，不由 prompt
或浏览器 flag 决定。

Agentic 行为必须有硬上限：

- 最多一次 query decomposition；
- 最多一轮补充检索；
- 子查询数量、topK、上下文 token、模型调用次数和总 deadline 均有预算；
- evidence 不足时拒答，不以第二次自由生成作为 fallback。

## 8. Responses API 与模型边界

Grok channel 的非敏感 contract probe 已确认：

- `/models`、`/chat/completions`、基础 `/responses` 可用；
- Responses `json_object` 可用；
- Responses strict `json_schema` 返回 403。

第一版采用：

- `MODEL__API_PROTOCOL=responses`；
- `POST {base_url}/responses`；
- `store: false`；
- 显式 `instructions + input`；
- `text.format.type=json_object`；
- 本地 Pydantic schema 严格校验；
- 校验失败最多一次 repair；
- 暂不使用 `previous_response_id`；
- 不静默降级到 Chat Completions。

用户问题与 evidence 必须 JSON 序列化为数据，system contract 明确它们不能
修改授权、工具、检索范围或输出契约。

## 9. 公开入口必须补齐的保护

当前匿名入口缺少可证明的服务端限流和完整总超时。接入更昂贵的 Agentic
RAG 前必须增加：

- 服务端字符、字节和 token 上限；
- IP + 匿名会话令牌桶、每日总预算和最大并发；
- RAG、模型和整体请求 deadline，并传播取消；
- fail-closed 的 public visibility、version、citation 与 canonical href
  校验；
- citation coverage 检查，不能仅凭 citations 非空就生成；
- cache key 绑定规范化问题、corpus snapshot、embedding/retrieval/model/
  prompt policy version；
- request id、输入 hash、snapshot、检索/模型成本、citation ids、拒答原因和
  latency 的脱敏审计。

浏览器不得获得企业 JWT、MCP context secret、模型 key、embedding key、
Qdrant key 或数据库连接串。blog facade 到 public agent facade 使用短期、窄
audience 的服务凭证。

## 10. 分阶段实施计划

### Phase 0：冻结现状与可恢复备份

- 识别 `blog-semi/.codex-patch-test`，不删除未知用户文件；
- 导出 public knowledge 源、Operator/InternalKnowledge 数据、Qdrant
  collections 和 pgvector schema/data；
- 记录旧 embedding model、dimension、collection、记录数和脱敏环境变量
  presence；
- 给每份导出生成 SHA-256 manifest，并做一次恢复演练。

### Phase 1：修复 enterprise 基础阻塞

- 修复完整 SQLAlchemy model registration 及 fresh-process regression；
- 为 publisher 增加 cycle timeout/supervision；
- 配置数据库 pool/probe budget；
- 让 staging smoke 输出阶段化且脱敏的失败证据。

### Phase 2：接入 Responses gateway

- 增加显式 protocol setting 和 Responses provider；
- 使用 Grok channel 的受保护 `MODEL__*` 配置；
- 完成 JSON contract、local validation、一次 repair 与 staging smoke。

### Phase 3：接入真实 embedding

- 恢复旧部署配置，或明确选择新的 provider/model/dimension；
- 实现 Python embedding provider/factory；
- 迁移当前 `vector(8)` 测试维度并创建新的 embedding generation；
- 用非敏感 eval corpus 比较 keyword-only、Hash baseline 与真实 hybrid。

### Phase 4：迁入 blog public corpus

- provision public tenant/system actor/membership；
- 实现 public snapshot importer 与 source URI metadata；
- 先以 PostgreSQL + pgvector 构建、校验并原子激活；
- 移植 Qdrant backend adapter、snapshot-aware collection/alias、导出恢复和
  对账能力，但默认不双写；
- 验证 public query 永远不能命中 enterprise tenant。

### Phase 5：实现公开 Agentic RAG

- 实现 query analysis/decomposition/rewrite；
- 实现 FTS、单一 vector backend、entity recall、fusion 和真实 rerank；
- 实现 evidence sufficiency 与最多一次补充检索；
- 复用 enterprise frozen evidence、grounding 与 citation gate；
- 增加公开限流、预算、cache、timeout、审计和安全测试。

### Phase 6：灰度切换公开助手

- blog 同源 endpoint 改为调用 Public Assistant Facade；
- 对旧 RAG 与新 Agentic RAG 做 shadow/eval，不向用户重复生成答案；
- 比较 citation precision/coverage、拒答率、P95、每问成本和超时率；
- 支持按 public snapshot 和服务路由快速回滚。

### Phase 7：精简 `blog-semi`

- 观察期稳定后删除站内 Operator UI、routes、LangGraph 与内部工具；
- 删除 blog RAG orchestrator、Qdrant/pgvector adapter、sync/eval 脚本；
- 删除旧服务和 RAG/model/vector secrets；
- 保留 public UI/facade；Studio/AI Daily 是否保留单独决策；
- 旧数据在通过恢复演练和保留期后再删除。

## 11. 验收标准

- 匿名浏览器只能访问 public facade，不能选择 tenant/version/corpus/tool；
- 公开查询在测试中无法返回任何 enterprise tenant chunk；
- 公开 execution context 只有 read evidence 能力；
- 弱证据或无证据稳定拒答，非拒绝答案具有可验证引用；
- 伪造、跨版本、未冻结或 excerpt 不匹配的 citation 全部被 gate 拒绝；
- snapshot N+1 未 ready 时仍查询 N，激活失败不影响 N；
- keyword 与 vector 读取同一 active snapshot/generation；
- ingestion 与 query embedding 的 provider/model/revision/dimension 完全一致；
- Agentic graph 在固定调用次数和 deadline 内结束；
- Qdrant 与 pgvector 切换有基准数据、对账证据和回滚步骤；
- blog 移除旧 RAG 后，公开助手仍通过同源入口正常工作。

## 12. 最终决策摘要

| 事项 | 决策 |
| --- | --- |
| 整个 blog RAG 是否迁移 | 是，迁移能力、数据与运维所有权 |
| embedding client 是否等于公开 RAG 链路 | 否，只是向量 provider 层 |
| 公开助手是否使用原内部工具图 | 否，重新实现只读 Agentic RAG 图 |
| 是否保留真正 Agentic 能力 | 是，新增分解、补检索、充分性与引用闭环 |
| Qdrant 与 pgvector 是否永久无约束双写/双查 | 否，一个 snapshot 只选一个 vector backend |
| 默认 vector backend | PostgreSQL + pgvector，先复用企业现有边界 |
| Qdrant 是否丢弃 | 否，迁移 adapter、数据导出/恢复和可切换能力 |
| `chatus` 是否接入 blog RAG | 否，保持独立 |
| `blog-semi` 最终保留什么 | 静态站、公开助手 UI、轻量同源 facade |

## 13. 尚需用户或部署侧确认的事实

1. 旧 blog 部署实际使用的 embedding provider、model 与 dimension；本地
   `.env.local` 没有这些值。
2. 旧 Qdrant 和 pgvector 实例是否仍可访问，以及 collection/table 的数据量
   与最后同步时间。
3. Studio 与 AI Daily 是否继续由 `blog-semi` 运行；它们与公开 RAG 迁移是
   独立决策。
4. public corpus 第一版是否接受“单逻辑 DocumentVersion 快照”；若必须保留
   多文档独立生命周期，则需要排入 `CorpusRelease` v2。
