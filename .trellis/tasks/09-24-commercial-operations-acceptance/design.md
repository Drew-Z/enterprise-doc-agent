# 当前环境验收设计

只读资源观测使用标准库远端探针（经 SSH stdin 运行，不落地文件）和本地 metrics 白名单解析器。探针仅调用 kubectl get、containerd content get、读取 /proc 与根盘空间、抓取发现的 API/Worker/Consumer Pod metrics；输出投影只含身份摘要、运行状态和观测字段，不返回 Secret 或配置明文。用 Pod 地址逐副本抓取，避免 Service 负载均衡隐藏副本；保留 Kubernetes Metrics API 自带 timestamp/window，不把重复读取旧样本视为新数据。采样只读、低频且有调用数和单次时限，逐次 journal 及最终结果存当前集中恢复组。

镜像核验区分同摘要、部署 OCI 索引到指定 Linux 平台 manifest，以及运行时归档索引包含部署索引与该平台 manifest 三类。索引字节校验 SHA 与 schemaVersion，目标平台唯一且成员 mediaType 正确才通过，保留三个摘要；不因摘要形式不同直接误判部署错误，也不无条件豁免差异。探针仍不证明签名、提交或迁移版本。前轮已部署旧版本的队列/Redis gauge setter 未接入生产调用，其新鲜度不可证明，对旧版本的汇总仍须保留 observation_incomplete；不能用导出的零值作为空闲证据。CO-3i 为候选补齐生产端及新鲜度后，仍须通过实际部署和采集验证，不能改写历史观测。

离线汇总将探针投影的镜像/配置/资源和 Pod 身份与第一次比较，检测漂移/重启/缺项；白名单指标的非有限值保持缺失。CPU/内存显示原始单位和实际限制，模型/对象延时只保留已有 histogram，不伪造零等待。通过可选业务报告关联阶段时间窗口，但业务报告没有实际主机身份绑定时不能宣称同目标。现阶段输出只读观测状态，production_capacity_approved 始终为 false；完整业务压测、真实浏览器恢复与最终放行另行验收。

复用现有部署、故障注入、质量评测、容量和恢复脚本。所有报告分别记录执行器SHA与服务镜像/运行配置，不用本地HEAD冒充线上版本。历史失败保留。

业务采样器独立于旧 health/Agent 容量报告：`business_capacity.py` 负责严格计划、HTTP 链路、任务/阶段时钟、分母与原始样本；`business_capacity_observer.py` 以同一已验证本地身份读取 DB/对象并调用现有 HybridRetrievalService；`run_business_capacity.py` 是显式本地执行入口。没有独立检索 HTTP 产品接口，因此不新增接口，不用生成总耗时冒充检索耗时；Core 检索样本的执行位置明确为采样进程。复用 Core 的 PacketView/ReviewInput、检索/引用及账本模型，不复制生产业务逻辑。恢复为新 HTTP 客户端读取和同幂等键重放，并独立检查一次操作/结算；它不证明真实浏览器或代理断网恢复。

本地执行仅允许回环 API/DB/对象端点和受控向量配置，要求确认目标 API 使用受控模型。各任务使用独立文件版本/响应表/幂等键，禁止失败后换键重试；签名对象请求用无应用凭据的独立客户端，验证完整 origin、无重定向，签名 URL、正文和异常原文不写结果。总时限覆盖流读取、轮询和观察器。逐任务账本检查按 attempt/job 标识，避免并发任务相互污染汇总差值。预期双路故障记录为业务失败且单独验收释放/调用上限。早期失败后剩余边界明确 not_run，未提交任务也保留。当前采样不发布旧格式 passed 证据，完整遥测、批准目标与主机实测留待后续。

质量：先修复unknown/unmet的可观察错误并独立审核冻结资料，再执行获准批次；不把网络失败从分母删除，也不在格式/语义失败时自动重采样。后台容错的受控边界测试与真实模型质量分开。

容量：4C4G、单后台并发；分别测控制面与供应商等待，上传/解析/检索/队列/恢复覆盖两轮以上。先提出准入/并发/超时目标，再跑数据；不得临时放宽目标刷绿。

恢复：复用现有backup_database.py、restore_database.py及DB/对象交叉引用验证流程，不配置备用服务器。准备独立数据库/对象命名空间的隔离演练，先核对目标、资源和隔离；主动备份仍集中到当前CODEX_HOME恢复组或明确获准的远程备份存储，不在业务目录旁新增副本。不得直接覆盖正式数据库演练。同机隔离测试不证明主机损坏后的恢复时间，旧独立故障域门槛保持待验收。

告警：外部探测不与业务节点共故障域；Prometheus保留已有配置，接收邮箱已在私有运维记录中指定。优先准备托管监控自带邮件通知；发送服务/账号尚未确定，不配置虚假投递。先完成具体发送配置与受控测试，实际故障/恢复回执才证明链路；没有实发授权时不发送邮件。

发布治理：精确CI名称和workflow路径跳过关系先复核，再形成main规则和Environment审核配置；应用后读回并保留回滚配置。当前单人操作者不能伪造独立审核。

本地恢复准备：显式 `--output-dir` 必须位于仓库外且尚不存在；本任务使用既有 CODEX_HOME 恢复组下的阶段证据目录。先查询目标不存在，再以 PostgreSQL `createdb` 的原子拒绝作为最终保护，不预先 `dropdb`。清理由成功创建后的所有权标记控制，在 `finally` 内执行；保留选项用于失败排查。所有 subprocess 都有限时。私有报告的 artifacts 使用本地绝对路径并标记用途，不满足正式门槛的仓库相对证据契约。告警先给出发送服务无关的具体接线/验收方案，待发送通道明确后再生成实际投递配置。

前提状态：公开响应新增可空 `prerequisites`，每项含 `condition/state/citationIndexes`，索引指向服务器验证过的 draft.citations（从 0 开始）。null/缺省仅表示旧数据未记录，空数组表示已记录且无前提。生成适配器在解析请求内引用时建立索引；合并引用仍逐字及按租户校验。JSONB 保存无需迁移。conditions 保留兼容投影，必须等于按顺序去重后的 unmet/unknown 条件，不靠词语猜测状态。

复核要求完整保留原稿的前提顺序、文字、出处绑定，只允许编辑状态；任一状态与原稿或最近一次复核不同必须有非空复核备注。服务器校验完整性、索引范围、总体状态和条件投影；旧稿只接受未记录前提。幂等指纹在前提为 null 时排除新字段以兼容升级前已保存请求。原稿和历史不可覆盖。UI 在前提旁提供对应证据，状态编辑后同步条件投影；旧稿保留自由文本编辑。CSV 增加有效及原模型前提状态/证据列，旧条件列改为中性标题。

评测新增 gateway-run-v3 记录结构化结果；离线评分严格校验 v3 原始响应与完整结果一致，历史 v2 只比较原契约投影并继续计入全部失败，不修改历史记录或评分含义。新旧前后端应一起发布，回滚到旧严格解析版本须注意新 JSONB 字段不被旧代码接受；本轮仅代码候选，不部署或回填历史数据。

逐项语义验收使用独立的 `score_presales_prerequisites.py`。参考文件绑定原始数据 SHA，每题列出稳定前提 key、业务描述、预期状态和来源原文锚点；人工映射文件绑定运行及参考文件 SHA，明确将各 key 对应到原始响应中的索引（遗漏为 null）。不由条件文字猜测对应关系。先复用原评分器校验完整运行，再仅对原本 succeeded 的 v2/v3 响应读取结构化前提；v2 的读取只用于新增回归分析，不修改旧存储结果或旧评分。各题均进入分母，失败/未执行/未复核均不能通过；遗漏、多余项、状态不符或该项引用未覆盖锚点均保留。正文语义与独立领域审核仍需另行完成。

v8 修改现有模型提示词及模型 schema 的展示顺序：prerequisites 排在 status 前、state 排在 condition 前。先对每个前提核验已完成、明确未完成或无法确定，再分别生成陈述、实际待办或确认问题，最后形成总分类。无关键词重写、自动修补、额外推理请求或字段契约变更；保留 v7 的引用选择、三态结构和输出限制。候选与试验计划均冻结具体哈希，未通过真实原始输出审阅不部署。

当前数据恢复切片：线上凭据仅在 SSH 子进程/执行器内存中使用。只读 repeatable-read 会话导出 PostgreSQL snapshot，表内容摘要与 pg_dump 共用该 snapshot；custom archive 直接流入集中恢复组。恢复到独占、本地无公网端口的 PostgreSQL 17；不包含 Supabase 管理 schema，也不尝试迁移线上数据库。

恢复应用切片复用私有快照和现有 ingestion 浏览器验收 harness。独占 PostgreSQL 17、MinIO 和 Redis 只发布回环随机端口；生成的本地凭据只进入子进程环境。数据库副本由 0027 升至当前候选 0031，历史数据保持可读取；所有新业务写入独立合成租户。原应用恢复点不改写，操作脚本及私有输出集中在原恢复组。先执行历史记录 API 检查，再运行实际上传/Worker/浏览器流程；不让消费者扫描恢复库的历史队列。

`scripts/local_object_recovery.py` 从恢复库的 `ObjectReference` 列表捕获对象。原 bucket/key 只进入私有 JSON，文件名是两者的 SHA-256；总量/对象数预先有界，下载逐块计算 SHA。仅 GET 源，不调用线上 Copy/Put/Delete。失败保留诊断目录且不发布成功清单。恢复端要求客户端实际 endpoint 为回环地址，所有目标桶为空；全量离线验证完成后按原 bucket/key 排他写入，再逐项读取验证。使用新的独占 MinIO 容器，原键可以保留而不改写恢复库；没有后台应用连接，不发送邮件或模型请求。临时数据在内存文件系统，结束后只停止并移除本轮独占资源。


CO-3i：Core jobs 模块提供只读队列年龄查询，分别从 pending/retry_wait 的现有 status/available_at 索引取第一项，再用数据库时钟计算最早已到期年龄；查询有 statement timeout，客户端采样每来源亦有截止时间。Core telemetry 的资源采样器接受显式读函数、registry 与时钟，不创建全局客户端；Worker composition root 连接现有 session_factory 与 Redis INFO clients，每次完成后间隔十秒，不追赶或并发叠加。业务监督器负责其取消与关闭。

Prometheus 新增服务端 redis_connected_clients，旧 redis_connections 保留为未测 NaN，不悄悄改变口径。queue/redis 两个固定 source 标签分别提供 resource_sample_success 与 resource_last_success_timestamp_seconds；初始化不制造成功记录，失败保持最后成功时间但值变未知。已有队列 setter 不产生新鲜度证明。只读汇总仅要求 Worker 提供此契约；API/Consumer 的资源 NaN 属于未承担的采集职责，进程指标仍需完整。旧线上没有时间戳仍为 observation_incomplete，新候选的成功标记、有限值及 45 秒内时间戳齐全时才解除对应缺口；production_capacity_approved 始终 false。


CO-3j1：复用现有部署 workflow 与只读主机入口，先固定当前工作区和五份旧索引。收尾步骤仍用 always 收集明确决策，但将 prerequisites/migration/workloads 的实际 outcome 作为运行输入；仅三者全部成功时才应用已部署候选的 workload manifest。任何 failure/cancelled/skipped/空值均不调用 kubectl，避免绕过迁移和前置条件。GitHub workflow 的 Bash run 原文在测试中真实执行，仅将 kubectl 替换为进程边界的记录器；Windows 明确使用本机 Git Bash 绝对路径，不调用 WSL。

发布文档区分预迁移恢复原部署、迁移中停止并核对数据库、迁移后使用 0031 兼容候选，以及开写后保留账本向前修复。候选来源/代码一致性与镜像签名、实际兼容回滚演练分别记录，不能因源码相同就填写镜像或业务恢复通过。默认 embedding rollout 和 authenticated smoke 都可能调用供应商，本轮零预算不得直接 dispatch 该流程。
