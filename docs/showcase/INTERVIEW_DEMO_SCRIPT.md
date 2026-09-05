# 企业文档 Agent 平台：5 分钟面试演示脚本

这份脚本用于本地演示或录屏。目标不是展示所有页面，而是用一条可追问的主链路证明：上传可恢复、入库可恢复、回答有证据、审批和发布可审计。

## 演示前准备

1. 按 [README](../../README.md) 启动 PostgreSQL、Redis、MinIO、API、Worker、Consumer 和 Web。
2. 准备一份无敏感信息的 TXT/DOCX/PDF 测试文档。
3. 确认 `http://127.0.0.1:5173` 可以打开，API 和 Worker 的 `/health/ready` 返回成功。
4. 如果没有真实模型路由，使用 local/test 的 deterministic provider；演示时明确这是编排和安全契约演示，不是模型质量证明。

如果设备资源不足以启动整套依赖，可以打开 `http://127.0.0.1:5173/?showcase=1#/overview`。这是只读的 `Showcase snapshot`：它用于讲解信息架构、状态模型、引用审查和产品交互，不连接 API，也不执行上传、创建、审批或下载操作。演示时应先说明这一点，再把实际工程能力落在代码、测试和已有证据上。

## 0:00–0:30：项目定位

口述：

> 这是一个企业文档 Agent 平台。我重点解决的不是把聊天页面接上模型，而是把大文件上传、异步入库、租户授权、带引用检索、人工审批和可恢复执行做成一条完整链路。API 负责控制面，文件正文直传对象存储，Worker 负责后台 Job，Agent 通过 MCP 获取授权证据。

展示：项目首页或 [PROJECT_SHOWCASE.md](PROJECT_SHOWCASE.md) 的一句话介绍和架构图。

## 0:30–1:15：平台 readiness

展示：平台首页的 PostgreSQL、Redis、MinIO 状态。

口述：

> 这里能看到三类基础依赖。PostgreSQL 保存业务事实，Redis 只负责唤醒任务，MinIO 是对象存储。即使 Redis 暂时不可用，Job 和 Outbox 仍然保留在 PostgreSQL 中，发布 lease 到期后可以继续恢复。

可追问点：为什么不把 Redis 当任务事实源？回答“Redis 的职责是协调和唤醒，不承担唯一业务状态”。

## 1:15–2:00：大文件上传与恢复

操作：

1. 进入 Documents，选择测试文档。
2. 创建 multipart upload，观察分片进度。
3. 暂停或模拟刷新，重新打开页面。
4. 展示已存在分片和继续上传后的 complete。

口述：

> 浏览器拿到的是受限 presigned URL，正文直接 PUT 到对象存储，API 不会把整个文件读进内存。刷新后前端从服务端会话和对象存储的已上传分片恢复，而不是从零开始。complete 还会校验分片清单、大小和 checksum，并处理并发 complete。

## 2:00–2:45：异步入库和故障恢复

操作：

1. 展示文档从 uploading 到 ingesting/ready 的状态变化。
2. 打开 Job/Attempt 或日志视图。
3. 如果现场允许，停止 Consumer，再等待 lease 过期后恢复 Consumer；否则展示已有测试证据。

口述：

> complete 只提交业务事实，同时创建 ingestion Job 和 Outbox 事件。Consumer claim 后拥有 lease 和 fencing token，定期 heartbeat。旧 Consumer 崩溃后，新 Consumer 可以接管；旧进程即使恢复，也不能用过期 token 覆盖新结果。这是业务幂等和 Celery ack 之外的一层保护。

## 2:45–3:45：Hybrid RAG 与 Agent run

操作：

1. 进入 Agent runs，选择 ready 的文档版本。
2. 输入一个能由文档回答的问题。
3. 创建 run，展示事件流、引用和最终答案。
4. 展开 `Execution metadata`，指出模型/版本、token 用量、provider 请求、fallback/breaker 和执行序号与本次 run、artifact 属于同一条可复核链路。

口述：

> Agent 不是直接把整篇文档塞给模型。MCP 的 search_document 先做租户授权，再把关键词召回和向量召回用 RRF 融合。系统冻结本次授权证据，模型只能基于这组证据回答。引用不在候选集、证据不足或结构化输出不符合契约时，系统会拒答或失败。

> 运行面板还会保留模型与行为版本、token 和 provider 调用计数、retry/fallback 结果以及 breaker 状态。这样面试官看到的不只是“回答是什么”，还可以继续追问“由哪个版本、经过几次调用、是否发生降级、最终产物如何对应”。

可追问点：当前 embedding 是否等于生产语义质量？回答“当前 deterministic/hash embedding 主要用于可重复编排和契约验证；真实语义质量仍需真实 Provider 和代表性语料 gate”。

## 3:45–4:30：人工审批与 artifact

操作：

1. 创建带“发布”选项的 Agent run，或使用已有高风险任务。
2. 展示 approval requested 状态。
3. 以授权用户批准或拒绝，观察 run resumed 和最终状态。
4. 展示 verified artifact 下载入口。

口述：

> 高风险回答会在 LangGraph interrupt 暂停，等待精确目标上的人工审批。审批接口做幂等和越权校验，恢复后才允许 publish。最终产物需要再次经过数据库和对象元数据校验，下载时获取短期 URL，而不是长期暴露对象地址。

## 4:30–5:00：工程证据与边界

展示：Audit log 的治理时间线，然后展示测试报告、证据索引和 [PROJECT_SHOWCASE.md](PROJECT_SHOWCASE.md) 的边界章节。

口述：

> Audit log 会按当前租户记录文档、Job、Agent run、审批和 artifact 的治理事件，支持 action、资源、actor、时间范围查询、游标加载历史和 CSV 导出。认证 middleware 对失败请求写入不含凭据的结构化安全日志，但不会把每次无状态 bearer 请求伪装成 login；本地 JWT 通过 `POST /api/session/logout` 写入 tenant-scoped revocation 和 `auth.session.revoked` 审计事件。当前工作树非集成回归是 1006 passed，成员、绑定、live membership、受限 SCIM 和 local JWT revocation 相关 PostgreSQL 身份集成链路是 7 passed，完整 integration suite 是 125 passed；其余数据库和浏览器集成回归按环境单独执行。项目还有 Kubernetes、镜像签名、迁移、探针和回滚契约；服务端已实现 JWKS-backed OIDC token verification、owner-only 手工成员 provisioning/deprovisioning、显式 issuer/subject binding 管理，以及受限 SCIM discovery、Users 分页/等值过滤、50 操作串行 Bulk（含受限 PATCH）、仅支持 `replace active/userName` 的 PATCH 与 user upsert/deprovision。需要明确的是，完整 SCIM PATCH 语义/bulkId、复杂 filter、批量 IdP 同步、首次登录自动 provisioning、端到端 SSO、外部 IdP logout、真实 Provider 可重复 40-case、代表性容量、托管 observability、独立故障域恢复和 GPU/vLLM 仍是外部 gate。我会把它定位为 CPU staging-capable 的工程项目，不把本地审计 fixture 或本地证据说成生产容量。

## 面试官追问速答

**为什么不用一个 FastAPI 进程完成全部工作？**

因为上传、解析、embedding 和 Agent 执行的耗时与失败模式不同；拆开后可以分别限流、重试、恢复和观测。

**为什么需要 Outbox？**

避免数据库事务提交成功但消息发布失败，或者消息先发布但数据库事实未提交。Outbox 让业务提交和待发布事件绑定在同一个事务里。

**为什么还要 MCP？**

它是 Agent 到数据能力的显式工具边界，可以集中做签名上下文、租户授权、工具参数校验和审计，而不是让模型或 Agent 直接访问数据库。

**项目是否已经生产可用？**

核心实现和 CPU staging 基础已完成，Audit log 具备租户隔离的查询、分页、导出，以及 owner-only retention/legal hold 治理控制面；归档执行会生成带 SHA-256 校验和的可恢复 JSON 快照，并提供批次验证和受控下载，不删除源事件。WORM/独立存储、自动化跨区域恢复、删除证明、生产容量、托管运维、导出权限扩展、独立灾备和 GPU 证据尚未闭环，这些边界在 evidence/gates 中有明确记录。

**当前已经有一台 4C4G 单节点机器，下一步做什么？**

当前代码、镜像 digest、模型版本和 4C4G staging 验收记录已经固定；下一步是在获得批准的代表性语料和 provider revision/cost 信息后，以低并发完成真实质量重复性基线，再补 managed observability ownership，最后在独立 fault domain 做恢复演练和人工复核。当前单节点可以做受控 staging，但不能证明 HA 或灾备。
