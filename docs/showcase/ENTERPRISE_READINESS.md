# Enterprise Readiness Boundary

这份文档是项目展示和面试沟通的边界说明。它把当前仓库已经验证的能力、已经落地的最小企业策略，以及仍需要外部条件或后续开发的部分分开记录。

## 当前授权策略

当前身份模型只有两种租户成员角色：`owner` 和 `member`。

| 能力 | `owner` | `member` | 当前证据 |
| --- | --- | --- | --- |
| 查看当前租户审计事件 | 允许 | 允许 | `GET /api/audit-events`，服务端始终使用认证 principal 的 `tenant_id` |
| 导出审计 CSV | 允许 | 拒绝 | `apps/api/src/enterprise_doc_api/audit/router.py` 中的 `AuditExportForbidden` |
| 高风险 Agent 审批 | 允许 | 拒绝 | 现有 approval service 的 owner 检查 |
| 文档级 ACL | 可管理全部文档策略并访问受限文档 | 可访问租户可见文档；受限文档需是创建者或获得用户/角色 grant | `document_visible_to_actor`、`/api/documents/{id}/access`、`/grants`，以及 PostgreSQL ACL 集成测试 |

审计查询和导出不会接受客户端提交的租户 ID。导出权限在路由调用审计服务前检查，因此被拒绝的成员请求不会触发数据库查询。

文档默认保持 `tenant` 可见；显式切换为 `restricted` 后，服务端会在 inventory、关键词/向量检索、Agent 创建与后续工具调用、run status/events、artifact preview/download 上应用同一 SQL 授权谓词。文档创建者和租户 owner 保留管理权，用户 grant 与 `owner`/`member` 角色 grant 均可审计；撤销 grant 后，旧 Agent 执行上下文的下一次工具调用也会重新鉴权并被拒绝。

本地 Playwright 还使用两个独立浏览器上下文和同租户 owner/member 两个真实 principal，完成了 `multipart 上传 -> restricted -> 用户 grant -> member 可见 -> owner 撤销 -> member 刷新后不可见` 的端到端验证。该证据走真实 API、PostgreSQL 和 MinIO，不依赖前端路由 mock；它证明当前本地授权链路，不等于外部 IdP 或生产 IAM 验收。

这是一项可审计的文档 ACL 与最小 RBAC 策略，不应描述为完整 ABAC 或企业 IAM。Documents 页面已经提供访问模式、用户 grant 和角色 grant 管理抽屉；任意属性表达式、外部 PDP、IdP group provisioning 以及连接器资源权限仍是后续工作。

## SSO 接入契约

当前仓库验证的是本地 JWT bearer 流程：token 包含租户和 actor 标识，服务端通过 active membership 解析角色。服务端现在提供了可注入的 external principal adapter 契约，并实现了 provider-agnostic 的 JWKS-backed OIDC JWT decoder：校验签名算法、`kid`、issuer、audience、`iat/exp`，再标准化 subject、tenant、actor、groups/role。非 UUID 的标准 OIDC subject 通过显式 `(issuer, subject) -> user` 绑定解析，不按 email 自动匹配；最终角色仍由服务端 active membership 复核。默认仍关闭外部认证；开启 `external_auth_enabled` 后可通过 `external_jwks_url` 自动装配，也可注入自定义 resolver。

外部角色映射现在是显式、可配置且冲突安全的：`external_owner_groups` 和 `external_member_groups` 由服务端配置，启动时拒绝空白、重复和重叠组名；一个 token 同时命中 owner/member 组，或启用 role claim 后 role 与组映射冲突，都会拒绝访问。`external_role_claim_enabled` 默认关闭，因此 IdP 的 `role` claim 不会在未评审时直接改变应用角色；即使显式开启，数据库 active membership 仍是最终授权事实。

在此基础上，服务端提供了一个受限 SCIM 契约：`GET/PUT/PATCH/DELETE /scim/v2/tenants/{tenantId}/Users/{subject}` 使用每租户高熵 bearer token，按配置的 issuer 和 group 映射在单事务内幂等创建、恢复或停用 membership 与 external binding，并写入 `scim.user.*` 审计事件；GET 用于核对单用户状态并返回 `lastModified`。同时提供 `ServiceProviderConfig`、`ResourceTypes`、`Schemas` discovery，Users 集合的有界分页和 `userName`/`externalId` 等值过滤，以及 `POST /scim/v2/tenants/{tenantId}/Bulk` 的最多 50 个串行 `POST`/`PUT`/`PATCH`/`DELETE` User 操作。单用户和 Bulk PATCH 只允许最多 8 个 `replace` 操作，路径限定为 `active` 或 `userName`，并复用现有单用户事务和授权边界；Bulk 每个操作独立复用该边界，逐操作返回状态。它不提供完整 PATCH 语义、bulkId 引用替换、复杂 filter、OAuth 授权服务器或真实 IdP 回调，完整 SCIM/IdP 端到端验收仍是部署 gate。

认证观测也有明确边界：API middleware 会对缺失、格式错误或内部解析失败写入不含凭据的结构化 `auth_failed` 安全日志；成功的无状态 bearer 请求不会被误记为“登录”。本地 JWT 已有服务端撤销表与 `POST /api/session/logout`，撤销记录按租户隔离、写入 `auth.session.revoked` 审计事件，并可在 token 到期后批量清理。Web 收到 session `401` 时会自动移除失效 token，并提示重新连接；登出接口不可达时也会清除浏览器 token，同时明确提示服务器端撤销未确认。外部 OIDC token 仍不能由本服务撤销，因此真实 IdP logout、前端回调和会话生命周期仍需集成验收。

API 的结构化失败响应包含稳定错误码和 `requestId`。Web 的 Upload、Agent、Documents、Identity、Audit 和 session 错误提示会保留这两个字段，便于把用户侧失败与服务端日志、审计事件和支持工单关联；不会在页面上展示 token、文档正文或敏感路径参数。

租户 owner-only 的成员与绑定控制面已经具备 API/UI：管理员可按邮箱有界搜索并手工 provision 成员、分配或调整 `owner`/`member` 角色、停用/恢复 membership，以及创建、停用和重新启用 issuer/subject binding。服务端禁止 owner 自我降级/停用并保证至少保留一个 active owner；membership 停用会同时停用该租户下的 active external bindings，恢复 membership 不会静默恢复旧登录绑定。有效生命周期动作保持幂等并写入审计事件。批量 IdP/SCIM 同步、首次登录自动 provisioning、SAML 和真实 IdP 端到端验收仍是后续 gate。

真实 PostgreSQL 集成测试 `tests/security/test_external_identity_binding_integration.py` 已覆盖 owner-only 管理、租户范围成员搜索、active membership 目标校验、重复 issuer/subject 冲突、绑定解析、停用后的解析失效、重新启用及生命周期审计事件；它验证的是本地数据库链路，不等于真实外部 IdP 的端到端验收。

真实 PostgreSQL 集成测试 `tests/security/test_membership_administration_integration.py` 已覆盖手工 provisioning 幂等、角色升降级、最后 owner 保护、离职级联停用 binding、恢复 membership 不自动恢复旧 binding、租户隔离和治理审计事件。

真实 PostgreSQL 集成测试 `tests/security/test_scim_provisioning_integration.py` 已覆盖受限 SCIM user projection 的幂等 upsert、Users 列表分页与等值过滤、邮箱更新、deprovision、跨租户隔离、SCIM 审计事件和最后 owner 保护；API 合约测试另覆盖受限 Bulk 的逐操作状态、操作上限和错误隔离，以及受限 PATCH 的 active/userName 更新和错误边界。它验证的是服务端事务与数据库边界，不等于完整 SCIM PATCH/bulkId/filter、批量同步或真实 IdP 验收。

正式接入 OIDC 或 SAML 前，需要明确并验证：

1. IdP issuer、audience、JWKS 轮换和 token 时钟容差。
2. 批量 IdP/SCIM subject provisioning、首次登录自动 provisioning 和上游离职同步；当前已提供受限 SCIM discovery、Users 分页/等值过滤、单用户 upsert/deprovision 契约，以及 owner-only 的手工成员生命周期、显式绑定管理和审计能力。
3. IdP group/claim 到租户 membership role 的映射与冲突处理。
4. 多租户选择必须由服务端 membership 决定，不能由浏览器或 prompt 传入。
5. 真实 session/IdP 方案确定后，登录、登出、密钥轮换、角色变更和拒绝访问的审计边界必须分别验收；当前已完成本地 JWT logout/revocation 审计，但不能把它表述为外部 IdP 的全链路 logout。

在这些条件完成前，面试中应表述为“服务端已实现可配置的 JWKS-backed OIDC token verification，并保留 provisioning 和真实 IdP 验收 gate”，而不是“已经完成企业 SSO”。

服务端配置使用嵌套环境变量；最小启用集合如下，默认值仍保持本地 JWT：

```text
AUTH__EXTERNAL_AUTH_ENABLED=true
AUTH__EXTERNAL_ISSUER=https://idp.example.test/
AUTH__EXTERNAL_AUDIENCE=enterprise-doc-agent
AUTH__EXTERNAL_JWKS_URL=https://idp.example.test/.well-known/jwks.json
AUTH__EXTERNAL_ROLE_CLAIM_ENABLED=false
AUTH__EXTERNAL_OWNER_GROUPS=["owner","tenant-owner"]
AUTH__EXTERNAL_MEMBER_GROUPS=["member","tenant-member"]
AUTH__SCIM_ENABLED=false
AUTH__SCIM_ISSUER=https://idp.example.test/scim
AUTH__SCIM_TENANT_TOKENS={"<tenant-uuid>":"<32-byte-token>"}
```

生产部署还必须把 issuer、audience、JWKS 轮换、claim 名称和组映射纳入变更评审；不要把 IdP 的 `groups` 或 `role` 直接当作最终授权事实。
JWKS 会短暂缓存以降低请求量；遇到未知 `kid` 时会执行一次受控刷新，避免正常密钥轮换必须等待缓存过期。

## 审计保留与归档契约

当前已经实现：

- append-only 的租户范围 `audit_events` 表；
- 按时间和事件 ID 的游标分页；
- 有界 CSV 导出；
- Agent run、Job 等关键流程写入治理事件；
- owner-only 的租户 retention policy（30～3650 天，默认 365 天且默认关闭）；
- tenant-wide 或 resource-scoped legal hold，支持过期、列表、幂等释放和治理审计事件；
- retention preview，按 SQL 统计 eligible/protected 历史事件；
- owner-only retention plan dry-run，固定 cutoff、返回有界候选事件 ID 和 fingerprint；
- owner-only retention archive 执行，按稳定 fingerprint 生成已校验 JSON 快照并记录 archive batch，源审计事件保持不删除，重复请求幂等；
- 支持按租户列出最近 archive batch，并通过对象头信息、回读 SHA-256、大小和 JSON envelope 重新验证归档完整性；验证结果写回治理审计事件；
- owner-only 归档下载入口在签发 presigned URL 前再次校验对象大小与 SHA-256 metadata，下载行为写入治理审计事件；
- 审计页面中的中英文治理面板，展示模式保持只读。

仍未形成生产合规闭环：

- 自动化恢复/回放流程、加密策略和恢复演练；
- retention job 的运行指标、失败重试和删除证明；
- 独立审计存储或 WORM 合规证明。
- retention archive 当前只写入可恢复快照，不执行源事件删除；WORM、跨存储复制和删除证明仍是后续 gate。

当前实现是治理控制面和可恢复归档执行，不会自行删除审计事件。生产策略仍需接入 WORM/独立存储、跨区域复制、恢复演练和删除证明，并在任何删除前保留 policy version、执行者、时间窗口和归档对象校验信息；在这些证据条件完成前，不能承诺具体合规认证。

## 部署和容量边界

本地和当前单节点 4C4G staging 路径可以验证迁移、探针、Job/Outbox、认证业务 smoke 和确定性 Agent 契约；仓库另有 2C/2GiB tiny profile，主要用于 readiness 或隔离探针。单节点验证不能证明：

- 生产 QPS、并发 Agent run 或真实模型质量；
- 多节点高可用、独立故障域恢复或 RPO/RTO；
- GPU/vLLM、量化模型或长上下文容量；
- managed observability、告警投递和事件响应值守。

当前 4C4G 设备足以继续控制面、权限、审计、UI 和 CPU staging 验证；真正缺少的是备用节点，因此不能证明节点故障恢复、零停机升级、RPO/RTO 或多故障域灾备。真实模型容量矩阵和灾备演练应在具备相应基础设施后再执行。Swap 只能作为事故缓冲，不能作为容量证据。

## 面试中的准确表述

推荐表述：

> 我把租户隔离、owner/member 最小 RBAC、成员 provisioning/deprovisioning、受限文档 ACL、审计查询、受限导出和 retention/legal hold 控制面做成了可测试的服务端边界；授权条件在 inventory、检索、Agent 和 artifact 查询前下推到 PostgreSQL，并支持成员停用与用户/角色 grant 实时撤销。服务端已经实现 JWKS-backed OIDC token verification、显式 subject binding、owner-only 成员/绑定管理和 active membership 复核；批量 IdP/SCIM 同步、完整 ABAC、WORM/独立归档、生产容量和独立灾备仍保留明确的后续 gate，没有把单节点 staging 结果包装成生产结论。

避免表述：

- “已经完成企业 SSO”；
- “审计日志已经满足任意合规保留要求”；
- “member 可以导出全量租户审计”；
- “2C/2GiB 节点已经证明生产容量”。

## 后续优先级

1. P0：完成真实 IdP 选择、批量 IdP/SCIM 同步、首次登录 provisioning 和端到端验收；当前 role claim/group 映射已支持显式配置、启动校验和冲突拒绝，手工成员生命周期与显式 binding 管理已具备 API/UI、离职撤权和审计证据。
2. P0：确定 retention/legal hold 的合规要求，并补齐自动化恢复、删除证明和独立存储策略。
3. P1：按真实业务需要评估属性策略或外部 PDP；当前不把 ACL 夸大为完整 ABAC。
4. P1：在目标环境执行真实模型质量、容量和恢复演练，保存脱敏证据。
