# 多文档工作台实施与验证计划

日期：2026-10-11；更新：2026-10-13。状态：in_progress。A0–A4 本地工程候选、A1 扩展负例、A5 验证/跨领域留出资料、独立试用材料及留出/4C4G 执行器已完成内部验证；真实模型质量收益、4C4G 目标资源验收及独立 UAT 仍开放。
依据：[需求](prd.md)与[架构](design.md)。用户批准后已执行 task start，不执行自动部署或真实模型试验。

## 执行约束

复用规范仓库 `D:\workspace4Cursor\enterprise-doc-agent`，保留既有脏状态，不自动提交、push 或部署。每个切片开始前核对实际源码/依赖及适用规范；业务代码修改前保存恢复基线。所有新备份沿用集中恢复组，不能在源码旁生成副本。

优先做最小可运行闭环。主代理负责代码和最终验证；子代理只在宽重探索有收益时按用户要求单个、只读使用。设计中列出的新 API、模块和测试路径均待实现，不能把文档存在算成功能完成。

## 工作包与完成条件

| 包 | 内容及可演示结果 | 依赖 | 估算 |
| --- | --- | --- | --- |
| A0 任务与基线 | 冻结首包资料/来源许可、十字段模板、期望导出、错误分类、开发/验证/留出索引 | 架构评审 | 1–2 个有效工作日 |
| A1 资料与证据 | canonical blocks、DOCX/XLSX 结构与定位、解析限制、资料分组与快照；页面能看到原文定位 | A0 | 4–6 日 |
| A2 固定闭环 | analysis API/模型/Worker/结果矩阵，先固定检索直接回答；单主路、两槽、幂等恢复与单元隔离同时完成 | A1 | 5–7 日 |
| A3 有限 Agent | 一轮 search/read/calculate 动作，暂停/恢复与缺口；与 A2 在同资料/预算下对照 | A2 | 3–5 日 |
| A4 审阅与交付 | 版本化人工修改、精确审批、工作簿/待办交付、依赖失效与参数更新 | A2，完整验收含 A3 | 5–7 日 |
| A5 候选验收 | 独立材料、4C4G 目标资源、浏览器恢复、迁移与兼容回退、试用包 | A1–A4 | 3–5 日 |

合计约 21–32 个有效工作日，为单人沿用现有底座的工作量估算，不是上线日期。招募、客户数据授权与真实使用验证另计。A2 完成应已有可审阅矩阵，不能等所有工作包结束才展示产物。

A3 若没有可测收益，保留 A2 固定路径并关闭自主工具 flag，记录任务失败的原因；不为了 Agent 名称继续扩建。完整端到端的发布还须通过 A4/A5。

## A0：冻结行为与资料

- [x] 一个真实公开资料包的来源、下载时间、使用边界和内容 hash 可查；共同需求明确标为合成，Basecamp 缺价保持证据缺口。
- [x] 十字段清单包括范围、计价、数量/期间、税费口径、交期、支持与待确认，不将缺证据题删掉。
- [x] 开发、验证和留出包均冻结十字段及 30 行完整分母；reference 与 upload 物理分离且不进入模型输入。当前 reference 未独立标注，质量分数保持 null。
- [x] 验证和留出来源按供应商/原文件独立分组；开发包只作回归，足球球衣包仅作跨领域留出，不改变产品方向。
- [x] 记录当前本地为 deterministic provider、结构化上传与 analysis execution 默认关闭；不从历史 release 文档推导实时版本。
- [x] 文件大小、解析 watchdog、模型输入和 unit/run deadline 已进入版本化配置/策略；4C4G 实测仍归 A5。

## A1：建立可定位资料

- [x] DOCX 表格/合并单元格和 XLSX sheet/range 的公开解析输出已由真实文件测试验证。
- [x] 增加结构快照/coverage 的 additive migration；旧 generation 可读取。首切片采用 typed JSONB + chunk offsets，未建独立 block/span 表，见设计第 16 节。
- [x] 新 source 快照绑定所有版本/解析身份，schema 拒绝跨对象串源；真 DB 验证权限撤销、hash 损坏和旧解析器拒绝。
- [x] UI 资料区显示完整/部分/未知以及文件处理失败；原文查看提示未计算公式、隐藏内容和已检测的解析缺口。PDF 的未知覆盖不冒充完整。
- [x] 扫描页、公式无缓存、隐藏工作表/列、外部引用、路径穿越、文件/单元格/Office entry 超限及损坏压缩包均有显式负例。
- [x] 解析子进程超时与取消均回收；管道无额外临时文件，既有 spool 生命周期继续复用。新增真实子进程取消测试。

完成证据：同一结果的源字段可以从 DB locator 定位回原文件的正确版本；不要求模拟尚无 bbox 能力的视觉高亮。

## A2：固定取证矩阵与可靠执行

- [x] analysis 项目草稿/快照、run/unit/cell/action/event schema 与版本建立，first-party API 鉴权一致。
- [x] 创建事务包含 Job/Outbox、权益预留与初始事件；同幂等键重试返回相同 run。
- [x] 一次有界结构块检索、一个候选结果路径可展示全部三十单元；每项失败单独呈现。
- [x] 独立 `analysis.execution.v1`；主路身份冻结，备用模型配置不参与，旧 presales 策略语义不变。
- [x] 外部 HTTP 使用可控 transport 验证 429/503、发送前失败、未知超时与半截流。
- [x] intent 与 logical action/dispatch receipt 同事务关联；kill 后没有结果回执标为 unknown。
- [x] analysis gateway 单次只派发一次且无隐式 repair；重试、回答和后续扩展共用两槽。
- [x] DB 恢复使用真实 PostgreSQL，覆盖重复执行、租约过期接管、取消和迟到响应，断言 dispatch 与结算数量。
- [x] 服务端有限 SSE 支持 `Last-Event-ID`；浏览器刷新从历史 run/matrix 恢复且不新建任务。客户端当前仍用 GET polling。
- [x] 未引入 LiteLLM；保留现有薄适配器并关闭其隐式重试。

完成证据：无自主补查也可从同一输入得到可审阅矩阵；合法不足、失败、已完成不混淆。

## A3：有限工具循环与计算

- [x] final/tools/need_input/abstain schema；工具名、scope、数量和输出长度在 server 校验。
- [x] 第一次决策可执行至多一轮/三个工具；第二槽只允许终局协议。
- [x] 两槽同时覆盖重试：第一次重试消耗后，无第三槽用于工具后回答。
- [x] 已批准来源内 search/read、Decimal 白名单计算；文档提示注入不能改变目标或权限。
- [x] 等输入只持久化状态，不在 interrupt 前做不可重复副作用；恢复保留原 deadline/budget。
- [x] 公式输入、单位、税费缺口、舍入都可复算；输入依赖未知或冲突时阻止错误计算。
- [ ] 固定流程对照已完成同快照/路由/两槽的受控工程对照；真实模型下关键证据、人工修订、时延与费用仍未运行，不能据此判断 Agent 收益。

退出条件：若两轮候选均未减少主要错误或审阅投入，冻结扩展，回到证据/任务诊断。可保留可用的固定路径，不以循环步数当收益。

## A4：复核、导出与更新

- [x] 人工改写形成不可变 revision，审核只绑定精确版本，用户提供内容单独标记；失败/取消/过期且无模型结果的单元可从 revision 0 建立首个人工版本，原执行失败仍可见。
- [x] 新 analysis artifact/approval 覆盖来源集合，不使用任意首份 document_version 代替整个集合。
- [x] 草稿/复核稿及待确认项规范明确；未解决冲突/失败的表格不能显示为全部已满足。
- [x] 导出公式注入防护、静态计算结果、来源/算式索引、完整选定分母验证通过；相同矩阵的不同导出使用不同不可变对象键。
- [x] 0043 依赖指纹使参数/字段/上游变更只重算受影响范围；对象来源变化保守失效该对象全部依赖项，包括没有引用的“未找到”结果。跨快照复用不继承审核。
- [x] 权限撤销立刻阻止资料、矩阵、SSE 与下载访问；已有下载不可收回的限制有说明。
- [x] 精确审批/过期/重复发布、对象存储写入后 DB 失败均有幂等恢复路径。

审阅交付证据：真实 PostgreSQL/HTTP 与 Playwright r4 已展示模型原稿、人工版本、精确审核、完整 XLSX、批准、刷新恢复和权限复核；改一个字段后 9 项复用、1 项以 `field_changed:setup_fee` 重算，新 run 不继承审核，旧批准工作簿仍可读取。

## A5：候选验收与试用材料

- [x] 候选已冻结验证/留出材料及 30 项完整分母；留出 runner 将 fixed/agent 两臂固定为 60 项计划分母，并绑定 pack manifest、reference 与 upload 身份。reference 未完成独立全量复核时评分器固定返回 `blocked_reference_unreviewed`，不能按受控响应填充质量结果。
- [x] 留出评分器记录无需实质修改率、严重错误、缺失/冲突保留率、人工修订量、延迟、dispatch、token 与费用；未知 token/费用继续为 unknown，不按零处理。真实候选模型尚未运行。
- [x] 4C4G runner 已冻结 2 次 repetitions × ramp/steady_state/burst/recovery：共 14 次 analysis run、12 次 parse upload、最坏 168 次 model dispatch，并覆盖取消与幂等 replay。plan-only 只验证计划且固定 `production_capacity_approved=false`。
- [x] 外部容量证据必须绑定 CI release manifest 的 repository/ref/run_id/run_attempt/workflow_ref、部署 commit、api/worker/consumer/web 不可变镜像、SSH 资源快照和固定 correlation marker；首末日志计数必须从 0 单调到全部 runner HTTP 请求。
- [ ] 生成/embedding/诊断与评测调用先冻结预算并核对当日余额；不因测试数据量增加上限。
- [ ] 在真实外部单节点 4C4G 目标上测解析/生成竞争、峰值内存、排队、取消和恢复；再确认支持规模和等待时间。
- [x] feature flag 关闭 admission、排空/取消和历史兼容路径已有测试；0043 在依赖历史存在时拒绝破坏性 downgrade，拒绝后原 run 仍可读取。旧版本回退兼容演练已接入 CI，真实 runner 结果仍待取得。
- [x] Playwright r4 走完整任务、刷新恢复、原文复核、静态导出重读和增量更新；真实 API/隔离 PostgreSQL/受控 provider/store，浏览器运行真实模型调用为 0。
- [x] 试用包已备齐任务文件、操作说明、计时模板、反馈表、错误/补料/退出规则、数据与支持边界及验收记录模板。
- [x] 当前参与者为 0，状态明确为“内部验证与试用准备完成”，未称为独立 UAT。
- [x] 验证包和足球留出包各有 30 行助手审阅底稿，含原文行号、逐字摘录、条件、缺口和严重错误规则；冻结 reference/manifest/upload 哈希未变，助手底稿不计独立标注。
- [ ] 留出预检当前阻塞于 `analysis_auth_token_missing`、`analysis_execution_disabled`、`analysis_model_route_unconfigured`、`structured_uploads_disabled`；处理受控路由和预算后才能运行真实模型。

## 分层验证与 Mock 边界

| 行为切片 | 公开接口/观察点 | 真实边界 | 可替代部分 |
| --- | --- | --- | --- |
| 文档定位 | 解析适配器输出与证据 API | 实际 PDF/DOCX/XLSX 文件、规范化定位 | 不需要模型 |
| 权限/版本 | project/run/evidence/download HTTP | 真实 DB、租户/来源授权、ETag/快照 | 外部对象存储可用受控实现 |
| 预算/恢复 | 创建/取消/恢复 API + 持久 receipt | PostgreSQL 事务、Outbox、租约、结算 | 可控 HTTP provider，固定故障时机 |
| Agent 动作 | run/matrix 状态与 dispatch 数 | 真 graph/dispatcher/DB | 用受控模型响应覆盖分支；质量对照才用真实模型 |
| 人审/交付 | revision/review/export API | 真实文件生成和重读、审批身份 | 无需生成模型参与 |
| 产品质量 | 原始与复核产物、来源和计时 | 冻结材料与参考、真实候选模型 | 自动评分不能代替独立人审 |

新行为先写能因正确原因失败的边界测试，再实现与重构。不要只断言内部函数调用顺序。权限、账本与持久恢复不用纯 mock 来宣布通过。设计/导航等低影响文档编辑无需新增产品测试。

验证命令（analysis 目录已建立并执行；模型质量与生产资源检查另计）：

```powershell
Set-Location 'D:\workspace4Cursor\enterprise-doc-agent'
& '.\.venv\Scripts\python.exe' -B -X utf8 -m pytest tests/analysis/ -q
& '.\.venv\Scripts\python.exe' -B -X utf8 -m pytest packages/core/tests/test_model_routing.py -q
& '.\.venv\Scripts\python.exe' -B -X utf8 -m pytest tests/analysis/test_analysis_holdout_evaluation.py tests/deployment/test_run_analysis_capacity.py tests/deployment/test_validate_recovery_capacity_evidence.py tests/deployment/test_business_telemetry.py -q
& '.\.venv\Scripts\python.exe' -B -X utf8 scripts/run_analysis_capacity.py --plan infra/capacity/analysis-4c4g.example.yaml
& '.\.venv\Scripts\python.exe' -B -X utf8 '.\.trellis\scripts\task.py' validate '.trellis/tasks/10-11-document-analysis-workspace'
```

数据库、迁移和浏览器检查按现有仓库 harness 配置执行，实施时确认真实命令、数据库隔离及资源，不在此编造不存在的测试名称。变更触及的检查通过后不无故反复全量运行。

## 恢复点与本轮记录

本轮恢复组：`D:\Agent\codex\backups\tasks\20260924T015840.986Z-commercial-production-readiness`，manifest 阶段 `document_analysis_architecture_20261011`。

原父任务 task.json、旧路线导航文件已保存并核验修改前 SHA-256；本任务文件记录原先不存在。既有 2995 项脏状态不归本任务清理范围。规划完成只更新文档、任务元数据及研究索引，不改业务代码/依赖/配置，不执行产品模型调用或发布。最终文档与 JSON/链接验证结果见集中 manifest 与来源索引。

### 实施轮记录（2026-10-11 至 2026-10-13）

实施沿用同一恢复组，新增阶段 `document_analysis_implementation_20261011`；实施开始基线为 3001 条脏状态。实体快照、经过验证的 Git 恢复点和原先不存在的路径逐个登记，不覆盖历史基线。

已交付：

1. parser 2 的 TXT/PDF/DOCX/XLSX canonical structure；嵌套表格顺序、合并格、隐藏列分组、公式及缺口；隔离解析和真实 DB 摄入恢复。
2. 默认关闭的上传开关、XLSX envelope、冻结在 Job payload 的解析器版本及 Worker 路由；恢复不因开关改变而改用解析器。
3. 独立 analysis 草稿/对象/十字段模板/来源快照；ETag 防覆盖、同 revision 确认幂等、所有来源权限交集、不可变 hash 与保留关系。
4. `/api/analysis` HTTP 边界和 `#/analysis` 工作台；资料分组、选择/编辑清单、保存、确认、历史来源、原文定位/覆盖缺口；未保存编辑不能确认旧草稿。
5. 两个候选迁移 0038/0039；隔离 PostgreSQL 下 upgrade/空库 downgrade/model 对齐/有历史拒绝 downgrade 已验证，没有对 public 或生产运行迁移。

资料准备阶段验证：analysis 专项 21 项通过（含真实 PG、HTTP、迁移、摄入恢复和上传→Worker→快照）；后端非集成 3375 项通过，23 subtests；全量 `pnpm test` 通过；Ruff、跨包 Mypy（294 文件）、前端 lint/typecheck/build 通过。后续 A2 结果见下一段和集中 manifest。

浏览器用真实 API 与隔离 PG，完成分组、保存、确认、读取原文、刷新后重新选择项目、390px 窄屏无横向溢出。截图/报告在集中证据目录 `analysis-browser-20261012-pass3`（后续视觉细化见验证索引）。测试源为明确合成资料；公网模型、商业质量、正式浏览器身份登录链和真实对象存储不在本轮浏览器验证范围。fixture 显式移除自己创建的 PG schema，测试服务退出。

已知验证提示：迁移 metadata 比对会报告原有 upload_sessions/document_versions 双向 FK 的 SQLAlchemy 排序 warning；生产构建提示主 JS chunk 超过 500 kB。两者未被隐藏，本切片没有据此宣称容量达标。

### A0/A2 实施记录（2026-10-12）

公开开发包 `materials/development-01` 包含 Asana Starter、Trello Standard、Basecamp Studio 六份官网提取文本和一份合成共同需求。完整分母 30，初步标注 9，未标注 21，质量分数保持 null。A2 完成时只有该开发包，验证和留出尚未准备；截至 A5 已增加一个软件采购验证包和一个足球球衣采购跨领域留出包。开发资料已参与开发，只能用于回归。

迁移 0040、Core execution、Worker handler、run/matrix/cancel/有限 SSE API 和 React 矩阵已实现。固定来源快照内每对象×字段建立独立 unit，服务端验证类型、逐字引用和来源范围；共同需求不证明供应商，不跨对象串源。固定单主路，每 unit 至多两次派发；只有连接前故障及明确 429/502/503/504 可同路重试，未知超时/截断流不自动重发。合法结果结算一个 `analysis_cell`，执行失败释放，waiting_input 暂留；正式运维 CLI 可显式配置该额度，旧两指标配置保持迁移兼容。

验证：`tests/analysis` 42 项通过并保留 2 个既有双向 FK 排序 warning；权益/正式 CLI 定向 63 项通过；旧 billing schema 70 项通过；Ruff 320 文件通过，Mypy 303 文件通过；Web lint/typecheck、59 文件 463 项测试和生产构建通过。全量非集成最终为 3382 passed、875 deselected、23 subtests passed，文档契约缺口已消失。构建仍提示已知 590.53 kB 主 chunk warning。

### A3 工程切片记录（2026-10-12）

迁移 0041、版本化 `analysis.execution.v2` / `analysis.agent.v1`、输入请求与工具回执已经接入。固定模式仍是默认并保持 v1 协议；Agent 模式显式选择后，首槽最多提交三个 `search_sources` / `read_source` / `calculate`，第二槽只接受终局动作。搜索和读取绑定冻结对象来源，计算使用 Decimal、白名单操作、显式单位/舍入和来源回执；已完成工具不会在 Job 重放时再次执行。

`POST /api/analysis/runs/{id}/inputs` 经过原身份与全来源权限检查，要求 request ID、expected revision 和完整参数集合。等待输入不占 Worker lease、不结算或释放原 reservation；有效提交生成唯一恢复 Job，保留 run/unit deadline 和剩余派发槽。同值重放不新增 Job，改值/旧 revision/外来 request 拒绝。React 提供 fixed/agent 模式选择、模式隔离幂等键、等待项输入表单、提交后立即刷新并在 queued/running 时继续 polling；矩阵显示服务器计算算式。

受控对照见 `a3-comparison.json`：固定与 Agent 使用同一 snapshot、冻结 route/model 和每单元两槽上限。固定流程完成于 1 次派发且 0 工具；Agent 完成于 2 次派发和 1 次 `search_sources`。独立恢复用例在 2 次派发内持久化 `1200 * 10 * 12 = 144000.00 CNY`，重复提交仍只有一个恢复 Job。该证据只验证工程边界；真实模型正确率、关键证据增益、人工修订、外部时延/费用和客户验收均未运行，保持 null / not_verified。

A3 定向验证为 24 个原协议/执行/API 测试、2 个前端矩阵测试和 2 个对照/恢复测试通过；完整门禁结果在本任务收尾时更新。当前实现使用 `AnalysisExecutionService` 内显式持久状态机，没有接入 LangGraph checkpoint，不把设计候选框架写成已实现事实。

### A4 审阅与交付实施记录（2026-10-12）

迁移 0042、`AnalysisReviewDeliveryService`、静态 XLSX 生成及 analysis 专属 artifact/approval 已接入。人工改写保存新 revision 并标记 `user_provided`，不覆盖模型原稿或伪造文档证据；审核绑定精确 revision/content hash，矩阵只投影当前版本。模型失败、取消或过期且没有结果的终态单元可以从 `If-Match: "0"` 创建首个人工版本，原执行状态和错误码继续保留。

草稿冻结完整分母；复核稿要求每个当前 revision 已批准。工作簿固定包含 `Comparison`、`Pending Items`、`Sources & Basis`，重新打开检查且不含公式，危险字符串加前导单引号。artifact 绑定完整来源 manifest、matrix hash 和精确 cell revision IDs；对象键同时包含 matrix revision 与内容 hash，相同矩阵的多次合法导出不会互相覆盖。对象写入后若最终 DB 提交回滚，同一幂等键复用同一 writing artifact 完成恢复。

下载在对象元数据检查前后两次复核全部来源权限，复核稿还要求未过期的精确批准；撤销来源访问后不再签发 URL。React 支持修改、版本历史、审核、草稿/复核稿、批准与刷新恢复；失败单元可人工建立首版本。Playwright 用真实 API、隔离 PostgreSQL、受控模型和受控对象存储完成 10 单元取证到交付全链路，并验证 390px 无横向溢出。该证据不代表真实模型质量、正式身份提供商、生产对象存储、4C4G 容量或独立客户验收。

本轮专项验证：`tests/analysis` 55 项通过并保留 2 个既有双向 FK 排序 warning，Web 59 文件 466 项通过，Ruff format/check、跨包 Mypy（309 个源码文件）及 Web lint/typecheck 通过；生产构建通过并保留 605.81 kB 主 chunk warning。A4 Playwright 1 项在桌面与 390px 窄屏通过且清理隔离 schema。完整后端非集成为 3385 passed、885 deselected、23 subtests passed，运维 monitor 103 项通过。

### A4 增量与 A5 工程候选记录（2026-10-13）

迁移 0043、依赖指纹和显式 `base_run_id` 已实现。复用要求依赖、结果及 evidence receipt 全部完整；来源范围变化会保守失效此前没有引用的缺失结果。浏览器增量用例改动 `setup_fee` 后复用 9 项、重算 1 项，新任务不继承审核，旧批准工作簿仍可读取。Playwright r4 通过并清理隔离 schema，真实模型调用为 0。

A1 新增 XLSX 外部链接、损坏压缩包和 Office entry 超限 typed-error 负例；既有扫描页、公式缓存缺失、隐藏内容、路径穿越、文件与单元格上限共同形成机器可读覆盖记录。0043 存在依赖历史时拒绝 downgrade 并保持原 run 可读。材料/解析阶段测试为 19 passed；最终解析、材料及迁移定向测试为 18 passed、2 个既有双向 FK 排序 warning；PostgreSQL 恢复后的最终 `tests/analysis/` 后端全集为 67 passed、2 warnings。

A5 冻结 `validation-01` 软件采购包与 `holdout-football-01` 足球球衣采购留出包。两包均为 3 对象 × 10 字段、6 个官网来源和 1 份合成共同需求，reference 为 30 行且不进入上传目录；正文与集中保存的 12 份原始抓取 JSON hash 可重算。足球材料只扩大试用范围和验证泛化，不将产品改成足球专用 Agent。试用材料已经就绪。

A5 留出执行器、评分器与 4C4G runner 已完成本地工程验证。留出执行绑定 pack/reference/upload SHA-256 和完整分母；评分器在 reference 未独立全量复核时阻止质量放行。容量计划固定 14 次 analysis run、12 次 parse upload 和最坏 168 次模型派发，并显式覆盖 burst、恢复、取消与 replay。外部通过报告还必须由通用证据校验器重新打开原始 release/resources artifact，核对五项 CI release 身份、四个不可变镜像、单节点 4 CPU/约 4 GiB、Pod/Deployment ready、无 OOM，以及 runner correlation 请求计数从 0 单调增长至完整请求数。本轮组合测试 71 项通过；plan-only 输出 `production_capacity_approved=false`。

任务保持 `in_progress`：上述结果只证明 runner、评分和证据门禁可执行；真实候选模型的留出质量/用量/时延尚未运行，真实外部 4C4G 目标资源未验收，参与者仍为 0，独立 UAT、商业收益与付费验证均未成立。两个 feature flag 默认关闭；未执行生产迁移、部署或外部模型调用。

### A5 审阅准备增量（2026-10-13）

在不改动两包冻结 manifest、原 reference 和上传文件的前提下，新增两份各 30 行的 `reference/review-notes.json` 及审阅说明，并在试用包增加 `reviewer-guide.md`。60 行均覆盖完整对象 × 字段分母，证据只指向本对象冻结来源；审阅底稿记录助手身份、原文行号/摘录/文件 SHA-256、事实、条件、缺口和严重错误规则。该底稿只能降低独立审阅准备成本，不能由开发方助手自证为金标。

材料核验发现 Nike 退货抓取只有导航/地区列表、adidas 退货抓取为 `Page not found`。两者保留为覆盖缺口/干扰负例；adidas 商品页自身仍提供有限的 30 天免费退货和定制不可退换事实。PUMA 仍以冻结正文的 delivery 后 30 天为准。软件包保留套餐继承、Enterprise 越界、促销续费、税费和退款条件的判错边界。

本地留出预检没有网络或外部模型调用，返回四项阻塞：analysis token 缺失、analysis execution 未启用、固定模型路由未配置、structured uploads 未启用。因此尚未产生 fixed/agent 真实质量数据；下一执行门是在既有受控 staging/模型配置中完成短期 token、路由与预算核对，再运行冻结候选，而不是把助手底稿输入候选或据此调参。

只读 staging 探测确认现有目标仍是 4 CPU / 3812556 Ki、API/Worker/Consumer/Web 各一副本就绪，且主模型配置为 `openai_compatible` / `gpt-5.6-sol`。但运行镜像早于 analysis 候选，ConfigMap 没有 `ANALYSIS__EXECUTION_ENABLED` 或 `UPLOAD__STRUCTURED_DOCUMENTS_ENABLED`，当前候选 HEAD 也未进入远端分支。故不能直接在旧镜像打开开关；须先整理并审核 analysis 变更、形成不可变镜像和发布证据，再以默认关闭部署，随后短期开启受控留出。

### A5 不可变发布预检（2026-10-13）

沿用现有 container → deploy-staging → rollback 发布链，未新增网关或第二套发布机制。基础 ConfigMap 和 staging renderer 显式固定 analysis dark 状态：结构化上传与执行均为 `false`，全局路由保持 `default`，unit/run deadline 为 120/3600 秒，单次模型输入上限 65536 bytes。执行开启时强制结构化上传先开启、模型 route deadline 存在且覆盖 unit deadline；release record schema v4 同步记录这些策略，防止渲染配置与发布证据脱节。

本地使用仓库要求的 Kustomize v5.7.1（`kubectl kustomize`）渲染真实 `single-node-4c4g` overlay，并用占位 HTTPS 端点和四个不同的占位 sha256 摘要完成配置及四阶段拆分。27 个资源被拆为 20 个 prerequisites、1 个 migration、1 个 embedding rollout 和 5 个 workloads；四个业务镜像均保持 digest-bound，迁移与 embedding Job 均使用 API 摘要，输出中没有 Secret。临时渲染目录已删除。部署/发布契约 224 项、M6/容量/恢复契约 55 项、隔离 PostgreSQL 迁移集成 1 项通过；本轮回滚兼容契约与脚本测试 22 项通过，Ruff、Mypy、workflow YAML parse 与 diff check 通过。使用 `rhysd/actionlint:1.7.7`（镜像 ID `sha256:b6d0bc67a629b20d424b3e35964400ec438451f41b95302157868d5e379d5039`）检查 `deploy-staging.yml` 与新增 `quality-heavy.yml` 均通过；全仓库扫描仍有 `quality-self-hosted.yml:29` 的两个既有 SC2086 告警，因此未将结论扩大为“全仓库无告警”。迁移测试保留 2 个既有 `document_versions` / `upload_sessions` 双向外键排序 warning。

0038–0043 采用新增表/列/索引的兼容扩展，0040 只放宽配额约束以加入 `analysis_cell`；有历史时 downgrade 主动拒绝破坏性删除。旧运行时代码没有启动时精确 head 检查，但尚未用真实签名旧镜像在 0043 上完成启动与 smoke，因此不能宣布旧镜像回滚已就绪。正式回滚目标必须先证明其可在 0043 上运行，或在任何 analysis 历史产生前使用已验证兼容候选；数据库降级不作为常规回滚路径。

机器可读结果见 `release-preflight-20261013.json`。状态仍为 `blocked_external`：rc.52 已 push/tag 并有四个签名 digest，rc.51 已登记为回滚候选；默认关闭部署/迁移、旧镜像 0043 兼容演练的真实 CI 执行、短期 token 与 `analysis-primary` 路由、真实 fixed/agent 留出、外部 4C4G 验收和独立 UAT 均未完成。本轮没有访问集群、调用真实模型或读取 Secret。

### A5 旧版本回滚兼容演练接入（2026-10-13）

新增 `scripts/run_analysis_rollback_compatibility.py` 与 `tests/deployment/test_run_analysis_rollback_compatibility.py`。脚本在临时 Docker network 中拉取固定 digest 的 PostgreSQL、Redis、MinIO 依赖，用 rc.52 API 镜像执行迁移与 checkpointer 初始化，确认 Alembic head `20261012_0043` 和 `analysis_projects`，再启动 rc.51 的 API、Worker、Consumer、Web 镜像并只记录健康/端口/OpenAPI 结果；无论成功失败都清理容器和 network，不写入随机凭据或原始日志。

`.github/workflows/quality-heavy.yml` 新增手动 `analysis-rollback-compatibility` job，固定 rc.52/rc.51 digest 并上传 run-scoped JSON 证据。兼容脚本与 CI 契约测试共 22 项通过，Ruff、Mypy、格式检查和 actionlint 通过；真实 GitHub-hosted runner 尚未 dispatch，因此 `release-preflight-20261013.json` 将回滚状态保持为 `pending_ci_execution`，不得据此宣布旧版本已可回滚。
