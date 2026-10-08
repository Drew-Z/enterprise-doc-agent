<div align="center">
  <img src="apps/web/public/auth-app-icon.svg" width="80" height="80" alt="DocAgent 文档盾牌图标" />
  <h1>DocAgent · 企业文档 Agent</h1>
  <p><strong>把企业资料变成有出处、可复核、可交付的售前响应。</strong></p>
  <p>文档上传与检索 · 售前问卷 · Agent 工作流 · 人工复核 · 租户权限</p>

  <p>
    <a href="https://github.com/Drew-Z/enterprise-doc-agent/actions/workflows/quality.yml"><img src="https://github.com/Drew-Z/enterprise-doc-agent/actions/workflows/quality.yml/badge.svg?branch=main" alt="Quality CI" /></a>
    <img src="https://img.shields.io/badge/status-public%20pilot-2563eb" alt="Public pilot" />
    <img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&amp;logoColor=white" alt="Python 3.12" />
    <img src="https://img.shields.io/badge/React-19-149ECA?logo=react&amp;logoColor=white" alt="React 19" />
    <img src="https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?logo=postgresql&amp;logoColor=white" alt="PostgreSQL with pgvector" />
  </p>

  <p>
    <a href="https://agent.playlab.eu.cc">试点站点</a> ·
    <a href="#quick-start">快速开始</a> ·
    <a href="#features">功能</a> ·
    <a href="#architecture">架构</a> ·
    <a href="#documentation">文档</a> ·
    <a href="https://github.com/Drew-Z/enterprise-doc-agent/issues">反馈问题</a>
  </p>
</div>

DocAgent 面向售前、安全问卷和企业知识核验场景。上传产品说明、制度或技术文档后，选择已就绪的资料建立响应表，逐条生成带原文引用的草稿，人工确认判断与措辞，再导出 CSV。需要多步处理时，可以使用带工具调用、执行记录和审批环节的 Agent 工作流。

**当前阶段：公开演示与受邀公网试点，正在完成受邀企业正式版。** 2026-10-08 18:10（北京时间）独立核验仍运行 [v0.1.45-rc.40](https://github.com/Drew-Z/enterprise-doc-agent/tree/v0.1.45-rc.40)，源码 `aeab213`、数据库 `0032`、售前后台并发 2，回退 rc.39。rc.41 发布失败保留。新候选 [v0.1.45-rc.42](https://github.com/Drew-Z/enterprise-doc-agent/tree/v0.1.45-rc.42) 固定在 `934d246`，签名、严格发布清单和四镜像缓存均已核验，尚未部署。外部缓存接收器修复 `69670cf` 的精确 CI 通过；两次中止及后续别名补齐分别留证。两份获准清理合计移除 148 个旧缓存引用，原五服务及回退缓存完整；本轮传输临时文件已清理，18:14 可用约 14.50 GB。下一步是独立 0032→0034 扩展、镜像切换及实际回退。现有 4 核 4 GB 单节点和外置依赖的范围保持。详情见[最终验收入口](docs/ops/final-project-acceptance.md)与[正式交付计划](docs/ops/commercial-production-plan.md)。

**公开演示已有完整公网流程的历史验收。** v0.1.43 覆盖 TXT 批量上传、解析、外部生成、原文引用、人工复核和 CSV；手机布局、访客隔离、刷新及退出也有实测。rc.1 在实际主机完成一次合成业务与同键重放。它们保留各自的版本和范围，不能替代最新候选的完整验收。见[演示手册](docs/ops/public-pilot-runbook.md#公开演示企业)。

**引用逐字校验已建立，业务命题仍需修正和独立复核。** rc.40 原六来源的技术链路与分类 6/6，严格正文、前提及引用 5/6；R5 混合并遗漏不同业务命题。原稿和人工修订稿需分别评分，助手审阅不算独立业务批准。历史失败见[质量记录](docs/ops/presales-quality-evaluation.md)，最新边界见[最终验收入口](docs/ops/final-project-acceptance.md)。

**完整人工前提修订已提交并通过 CI，尚未上线。** `fab3691` 可拆分、增补、改写、排除和恢复前提，逐项选择已保存证据并记录原项关联或人工新增；原稿、复核历史和 CSV 保留。新增 18 项真实数据库/API 回归及桌面/手机浏览器流程通过。需要数据库 0033；有修订历史时禁止删除该列降级，实际镜像回退留待发布验收。现网 rc.40 仍只有原状态修订能力。

**“自动/深度”候选已完成本地功能验证，真实两档对照仍待验收。** 新界面支持单行/批量选择，并从持久记录显示原档位；0034 保存受理时模型、推理、主备顺序和有效预算。刷新、重放、Worker 恢复及配置变更不会扩大派发次数或重置原截止，身份不匹配则明确失败。14 项策略数据库/API 测试、后台及普通浏览器回归通过，完整后端 2,787 项、前端 448 项通过。深度档提高推理及时间预算，其实际质量、时延、费用与同候选发布回退仍须验证；尚未上线。

此前 v4/v5 的分类、正文与前提问题，以及已撤回 v6 的引用和一致性失败，均保留在[历史评测记录](docs/ops/presales-quality-evaluation.md)。这些是助手编写合成资料的生成阶段试验，不代表公网端到端或真实客户准确率。

### 一键体验演示企业

打开[试点站点](https://agent.playlab.eu.cc/#/signin)，登录页同时提供“一键进入演示”和 GitHub 登录。每位访客自动获得独立的**演示企业**，资料、响应表、额度均归属该企业；同一浏览器刷新后可以继续，其他访客无法访问。

1. 点击“一键进入演示”，下载 [产品说明](apps/web/public/demo/product-guide.txt)、[交付说明](apps/web/public/demo/delivery-guide.txt)和[示例问卷](apps/web/public/demo/requirements.txt)。
2. 将两份资料一起上传，等待就绪；选择资料建立响应表，粘贴示例问卷。
3. 生成回应，展开原文证据、逐条保存复核，再导出 CSV。

演示企业有效期 2 小时，支持 6 个文件（单个 2 MiB、总计 10 MiB）、3 份响应表（每表最多 6 条要求）、6 次生成尝试，失败同样计数。退出或到期后无法继续访问，数据随后自动清理；请使用公开资料并及时导出结果。部署开关及全站预算见[公开演示手册](docs/ops/public-pilot-runbook.md#公开演示企业)。

## 界面预览

![文档工作区中的多文件、文件夹选择与上传队列](docs/showcase/upload-queue.png)

<details>
<summary>查看售前响应：原文证据、人工复核与历史草稿</summary>

![售前响应的引用与复核面板](docs/showcase/presales-review.png)

</details>

截图来自真实浏览器的**本地集成验收**，使用合成资料与受控模型，展示实际产品界面；不包含客户文档，也不代表公网模型质量评测。

<a id="features"></a>
## 可以做什么

| 能力 | 当前实现 |
| --- | --- |
| 文档接入 | TXT、PDF、DOCX；多文件与文件夹选择；顺序上传队列；不支持或空文件给出跳过原因 |
| 上传恢复 | 分片直传对象存储、校验和、暂停与续传；刷新后先查询服务端状态，已完成会话不必重新选择文件 |
| 资料检索 | PostgreSQL 全文检索与 pgvector 向量召回，通过 RRF 合并；检索结果受租户与文档权限约束 |
| 售前响应 | 选择就绪文档、粘贴要求、逐条生成结构化草稿；查看出处、补充条件与待补材料；保留原草稿和复核历史；导出 CSV |
| 生成恢复 | rc.40 后台执行并发 2；持久任务、刷新读取、部分成功、有限主备及逐次调用记录；最终候选仍须完整恢复验收 |
| Agent 执行 | 固定 LangGraph 流程、PostgreSQL checkpoint、内部 MCP 工具、SSE 事件恢复、人工审批与可验证产物 |
| 登录与协作 | GitHub OAuth 浏览器登录；应用会话与退出；企业准入、成员邀请、owner/member 权限、受限文档授权 |
| 公开演示企业 | 公网一键进入、每位访客独立企业、示例资料与真实业务流程；有期限与额度，自动清理；自行部署时默认关闭 |
| 用量与治理 | 企业试用周期与生成额度；用量记录；审计查询与 CSV 导出；保留策略、legal hold 与可验证归档 |
| 模型接入 | OpenAI-compatible Chat 与 Embedding 接口；Agent 主备路由与熔断；售前可独立选定路由和超时预算 |
| 交付与运维 | Docker 镜像、Kubernetes/K3s 清单、健康探针、GitHub Actions、镜像 digest、签名、SBOM 与发布证据 |

**Agent 成功任务额度、独立文档处理配额与逐次供应商调用记录** 已随 rc.1 部署。
企业用量页分别展示业务额度、存储和调用观察；超时或未知费用保留为未知，不合成为账单。
rc.1 当时为获准试点企业配置原周期内 100 个成功 Agent 任务和 100 MiB 文档处理额度；该历史记录不证明今天仍有可用周期或余额。新验收先核对实际有限周期、UTC 日余额和并发额度，不延长期限或重置用量。步骤见[平台运营手册](docs/ops/platform-operations.md)。

**典型操作路径**

1. GitHub 登录后接受准入或成员邀请，进入企业空间。
2. 上传资料，等待文档状态变为 **Ready / 就绪**。
3. 从文档创建售前响应表，录入需要逐条回应的要求。
4. 显式生成草稿，打开引用核对原文，再保存人工复核。
5. 导出草稿 CSV 或已复核 CSV，用于后续交付。

上传完成表示文件已收到，解析与索引仍可能继续。刷新页面后尚未上传的队列文件需要重新选择；文件夹路径只用于本地展示，不会创建服务端目录树。

<a id="quick-start"></a>
## 快速开始

### 先看界面

只需 Node.js 24 与 pnpm 11.9.0，在仓库根目录执行：

```powershell
pnpm install --frozen-lockfile
pnpm dev:web
```

打开 [本地只读演示](http://127.0.0.1:5173/?showcase=1#/overview)。页面会标注 `Showcase snapshot`，不需要数据库或模型服务，上传、生成、审批与下载等写操作不可用。

### 启动完整开发环境

前置依赖：**Python 3.12、uv 0.11.3、Node.js 24、pnpm 11.9.0、Docker 与 Compose v2**。以下命令使用 PowerShell，在仓库根目录执行。

安装锁定版本的依赖；首次配置时复制示例，保留已有 `.env`：

```powershell
uv sync --frozen
pnpm install --frozen-lockfile
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
docker compose -f infra/compose/docker-compose.yml config
uv run python scripts/foundation_smoke.py --preflight
```

启动 PostgreSQL/pgvector、Redis、MinIO，初始化对象桶和数据库：

```powershell
docker compose -f infra/compose/docker-compose.yml up -d --wait
docker compose -f infra/compose/docker-compose.yml --profile init run --rm minio-init
uv run alembic upgrade head
uv run enterprise-doc-checkpointer-setup --setup
uv run enterprise-doc-checkpointer-setup --check
```

分别在四个终端运行：

```powershell
# 终端 1：API
uv run enterprise-doc-api

# 终端 2：Worker 探针与 Outbox 发布
uv run enterprise-doc-worker

# 终端 3：任务消费者
uv run enterprise-doc-worker-consumer

# 终端 4：Web
pnpm dev:web
```

| 入口 | 地址 |
| --- | --- |
| Web 工作区 | [127.0.0.1:5173](http://127.0.0.1:5173) |
| API readiness | [127.0.0.1:8000/health/ready](http://127.0.0.1:8000/health/ready) |
| Worker readiness | [127.0.0.1:8081/health/ready](http://127.0.0.1:8081/health/ready) |
| API metrics | [127.0.0.1:8000/metrics](http://127.0.0.1:8000/metrics) |

这会启动开发服务，**不会自动创建可登录的试点企业或接通真实模型**。本地默认使用开发认证、确定性模型和 hash embedding；真实业务需要配置身份提供方、模型与 embedding，以及企业准入和额度。配置字段见 [环境示例](.env.example)、[浏览器认证契约](.trellis/spec/backend/browser-sessions.md)和[平台运营手册](docs/ops/platform-operations.md)。

也可以单独执行完整基础设施烟测：

```powershell
uv run python scripts/foundation_smoke.py --run
```

烟测会自行启动并检查依赖、迁移和应用端点，结束后停止其管理的进程与 Compose 服务，保留命名卷。应与手动启动模式分开使用。

停止手动开发环境的基础设施：

```powershell
docker compose -f infra/compose/docker-compose.yml down
```

<a id="architecture"></a>
## 架构

```mermaid
flowchart LR
    Browser[React 工作区] -->|会话与控制请求| API[FastAPI]
    Browser -->|预签名分片直传| Store[(S3 / R2 / MinIO)]
    API --> DB[(PostgreSQL + pgvector)]
    Publisher[Worker / Outbox] -->|读取持久任务| DB
    Publisher --> Redis[(Redis / Celery)]
    Redis --> Consumer[任务消费者]
    Consumer -->|解析与索引| DB
    Consumer --> Store
    Consumer --> Graph[LangGraph + 内部 MCP]
    Graph -->|检索 / checkpoint| DB
    Graph --> Model[模型与 Embedding 服务]
    Consumer -->|Embedding| Model
    API -->|默认同步售前生成| Model
    Publisher -->|可选后台售前生成| Model
```

- **API 管控制与权限，文件直传对象存储。** 上传内容不需要经过 API 进程中转。
- **PostgreSQL 保存业务事实。** Job、Attempt、Outbox、文档版本、权限和执行状态持久化；Redis 用于任务投递与唤醒。
- **任务可恢复，结果有归属。** 租约、心跳和 fencing 约束重复执行；模型草稿与引用绑定到具体文档版本。
- **人工复核是独立记录。** 原始草稿与复核内容分别保留，模型输出不自动成为已复核结论。

| 目录 | 职责 |
| --- | --- |
| `apps/web` | React、TypeScript、Vite；文档、售前、Agent、成员、用量和审计工作区 |
| `apps/api` | FastAPI 路由、认证会话、请求与权限边界 |
| `apps/worker` | Outbox 发布、可选后台售前任务、进程生命周期与探针 |
| `apps/mcp` | 内部 MCP stdio 协议与工具适配 |
| `packages/core` | 上传、入库、检索、Agent、售前、身份、权益与审计领域逻辑 |
| `infra` | 本地 Compose、容器镜像、Kubernetes overlays 与部署配置 |
| `scripts` / `tests` | 烟测、评估、发布校验、回归与集成验收 |
| `docs` / `evidence` | 操作手册、设计说明及带版本的验收证据 |

## 配置与部署

当前试点使用 **GitHub OAuth + 应用自身会话**，无需同机运行 Keycloak。代码还支持严格的 OIDC 校验；新增身份提供方需要单独验证协议、已验证邮箱声明和应用会话流程。

| 配置域 | 要点 |
| --- | --- |
| 身份 | GitHub OAuth App 使用准确的 `/auth/callback`；Client Secret 仅放服务端；登录后继续核验企业成员身份 |
| 存储 | PostgreSQL/pgvector 保存状态；S3-compatible 存储保存文档和产物；浏览器需要可达的预签名地址与匹配的 CORS |
| 模型 | 分别配置 Chat 与 Embedding；维度和索引版本必须一致；更换 embedding 需按手册重建索引 |
| 售前 | `PRESALES__GENERATION_ENABLED` 控制生成；`PRESALES__MODEL_ROUTE` 选择起始路由；模型超时小于整行超时；后台与自动切换分别显式启用 |
| 演示 | `DEMO__ENABLED` 默认关闭；标准发布还要求启用浏览器会话及售前生成；无需新增常驻服务 |
| 运营 | 准入、邀请、试用期限与额度通过正式运营入口管理；凭据不放前端或版本库 |

默认关闭后台生成及自动切换，`PRESALES__MODEL_ROUTE=fallback` 直接选择已配置的备用路由。启用 `PRESALES__BACKGROUND_GENERATION_ENABLED` 后，请求只提交持久任务，现有 Worker 在后台执行；刷新和切页不会重复提交。再启用 `PRESALES__AUTOMATIC_FAILOVER_ENABLED`，才会在连接、超时、限流或可恢复上游错误时尝试另一条已配置路由，同一操作最多两次调用，共享执行期限。引用、输出格式或业务校验失败不会自动重采样。

业务额度按一次成功操作结算，内部每次渠道调用单独记录并受日预算限制。超时仍可能在供应商侧完成和计费；未知用量保持未知。两路失败时保留资料与成功条目，允许稍后只重试失败项。上述配置默认值与现网状态分别判断：rc.40 已启用后台，并有真实主备和一次结算记录；完整候选验收仍待完成。配置、排空及回滚步骤见[后台生成手册](docs/ops/public-pilot-runbook.md#后台生成与有界渠道恢复)。

部署从 [4C4G 单节点手册](docs/ops/single-node-4c4g-staging-runbook.md)开始，使用已核验的镜像 digest 和配置清单。该 profile 将数据库、对象存储和推理外置，采用单副本及零 surge 更新；升级可能短暂中断服务。仓库还保留其他部署 overlay，不能把它们的资源预算当作当前主机配置。

发布链路：**Quality → Container Supply Chain → 镜像与配置核验 → staging 部署 → readiness 与业务验收**。签名、SBOM 和来源证明描述镜像构建与来源；它们不等于生产容量或模型效果保证。

## 开发与验证

```powershell
# 后端与前端基础质量检查
pnpm quality

# 单独运行前端检查
pnpm lint
pnpm typecheck
pnpm test
pnpm build

# 浏览器回归（先安装 Chromium）
pnpm --filter web exec playwright install chromium
pnpm --filter web test:e2e
```

基础 CI 与使用真实 PostgreSQL/Redis/对象存储的集成测试分别执行。浏览器、真实模型调用、外部部署和容量测试也有各自的环境要求；`pnpm quality` 不代替这些验收。

<details>
<summary>更多工程检查与历史里程碑</summary>

后端静态检查和非集成回归：

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy packages/core/src apps/api/src apps/worker/src apps/mcp/src
uv run pytest -m "not integration"
```

断点续传、Agent 安全与模型路由的独立验证入口：

```powershell
uv run python scripts/multipart_smoke.py --preflight --size-bytes 1073741824 --interrupt-after-parts 2
uv run python scripts/evaluate_m4_agent.py
uv run python scripts/evaluate_m5.py
uv run python scripts/benchmark_m7.py --scenario deterministic --iterations 20
kubectl kustomize infra/k8s/overlays/single-node-4c4g
```

历史 **M1-M7** 分别覆盖上传、持久任务、检索、Agent、观测评估、交付部署和模型路由。证据中的提交、环境、日期和验收范围共同决定结论；本地或确定性结果不能当作公网模型质量、生产负载或灾备结果。

迁移回退 `uv run alembic downgrade base` 仅用于可丢弃的本地数据库往返验证，会撤销全部迁移；不是线上发布的常规步骤。恢复与回滚见部署手册。

</details>

<a id="documentation"></a>
## 文档导航

| 你想了解 | 入口 |
| --- | --- |
| 上传协议、恢复状态与批量队列 | [浏览器上传](.trellis/spec/frontend/browser-multipart-upload.md) · [服务端完成语义](.trellis/spec/backend/upload-completion.md) |
| 售前响应与复核行为 | [前端工作区](.trellis/spec/frontend/presales-workspace.md) · [后端契约](.trellis/spec/backend/presales-workspace.md) |
| 登录、准入与企业运营 | [浏览器会话](.trellis/spec/backend/browser-sessions.md) · [平台运营](docs/ops/platform-operations.md) |
| 公开演示企业与资源限制 | [演示契约](.trellis/spec/backend/public-demo.md) · [启用与回收](docs/ops/public-pilot-runbook.md#公开演示企业) |
| 当前主机部署与模型配置 | [4C4G 手册](docs/ops/single-node-4c4g-staging-runbook.md) · [真实 Embedding 上线](docs/ops/real-embedding-rollout.md) |
| 设计取舍与历史展示 | [项目说明](docs/showcase/PROJECT_SHOWCASE.md) · [UI 设计](docs/showcase/UI_REDESIGN_NOTES.md) · [企业化边界](docs/showcase/ENTERPRISE_READINESS.md) |
| 回归与发布执行记录 | [GitHub Actions](https://github.com/Drew-Z/enterprise-doc-agent/actions) · [版本标签](https://github.com/Drew-Z/enterprise-doc-agent/tags) · [证据目录](evidence) |

历史展示稿和验收报告保留各自的日期与版本；当前功能以源码、配置和对应部署记录为准。

## 当前边界与后续工作

首版固定为受邀企业正式版。最后按 A 范围与状态统一 → B 完整人工修订及原子命题质量、C 自动/深度、D 空间与邮件前置 → E 同一候选综合验收 → F 独立审核、UAT 和交付推进。详见[正式交付计划](docs/ops/commercial-production-plan.md)。

- [x] 真实 GitHub 登录、企业空间与文档入库已在试点使用。
- [x] 多文件/文件夹队列、上传状态恢复、售前生成恢复与独立模型路由已实现并完成本地回归。
- [x] 独立演示企业、真实模型生成及已复核 CSV 导出完成本地和公网实测；第二访客隔离与刷新恢复通过。
- [x] 一键演示入口随 v0.1.43 上线，保留正式 GitHub 企业登录。
- [x] 公网完成两份 TXT 批量上传、三条响应生成、引用复核与 CSV 导出验收。
- [x] 六份复杂采购资料、五种判断状态完成两轮公网复测，公开原始结果、引用检查和失败记录。
- [x] 受控原文引用随 v0.1.44 上线；新冻结六题完成公网验证，公开误判、语言问题及用量。
- [x] 持久后台、有限渠道切换、恢复与调用账本已有本地及真实业务证据；当前 rc.40 / 0032，后台并发 2。
- [x] 当前四笔 ¥0.046616、六笔 ¥0.089334 消费已核对；新候选只补新增调用和一次结算验收，旧未知不填零。
- [x] 完整人工修订、自动/深度及持久预算策略已提交并纳入 rc.42 签名候选；本地功能验证通过，尚未上线。
- [ ] 修正并验收原模型原子命题遗漏，完成两档真实质量、完成率、时延和费用对照。
- [ ] 同一候选完成原 160 项容量、业务质量、浏览器恢复及发布回退验证。
- [ ] 处理 Outlook/Spamhaus 拒收，取得实际收件、值班反馈、独立审核及受邀客户 UAT。

备份、独立故障域恢复及 RPO/RTO 按已批准决定延期，明确为未验收；发布回退仍必需。公开支付和自助订阅留到后续。本次先后按两份批准删除 rc.30/rc.32–35 的 80 个、rc.21–25 的 68 个缓存引用，保留 rc.26–29、rc.36–40 和 Redis；完整峰值空间预检已通过，实际导入前仍须重新核验。

完整 SCIM/ABAC、外部内容连接器、公开远程 MCP 和 GPU/vLLM 部署尚未实现（not implemented）。现有受限 SCIM、文档 ACL、内部 MCP 与单节点 staging 各有明确范围。项目目前不承诺高可用、生产 QPS、WORM 合规或零停机升级。

## 反馈与贡献

欢迎通过 [GitHub Issues](https://github.com/Drew-Z/enterprise-doc-agent/issues)提供可复现的问题、使用场景或文档改进。提交问题时附上版本、操作步骤、错误码和请求编号；去除令牌、cookie 与真实企业文档正文。代码修改请附相关测试结果；新增能力同时更新对应文档。

**许可证：** 当前仓库尚未提供 `LICENSE` 文件，暂未声明开源使用授权；请勿将公开可读等同于 MIT 或 Apache 许可。
