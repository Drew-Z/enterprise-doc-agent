# 当前 4C4G 业务容量验收方案

状态：本地业务采样器已通过受控链路验证，当前主机的有界只读资源观测已执行；尚未执行主机业务容量试验。2026-09-25 的本地功能验证和只读低负载窗口都不能代替本方案；历史 4C8G readiness 结果也不适用。首版保留现有服务器，不扩容、不配置备用机。

## 先固定试验对象

- 分别记录执行器提交、服务器实际提交、API/Worker/Consumer/Web 的运行镜像摘要、ConfigMap 内容摘要及迁移版本。不得把本地 HEAD 填为线上版本。
- `run_application_capacity.py --external-execution` 现在要求 `--deployed-commit`。报告的 `commit_sha` 是声明的服务器版本，`executor_commit_sha` 是执行器版本；声明仍需与实际部署观测核对。
- 当前候选仍未部署。运行前须有具体部署与回滚方案、获准的隔离业务数据和试验窗口，不在用户正在使用的企业中压测。
- 先做受控模型的应用处理试验，再安排独立获准的真实模型试验。当前真实模型调用预算为 **0**；历史六次授权已用完。上传解析产生的向量请求也属于外部调用，不能漏算。

## 有界矩阵

以下是执行前的工程提案，尚无经营方批准记录，不能生成通过证据。两轮保留全部样本，失败不重采样，重复提交检查不另建业务任务。

| 阶段 | 每轮完整业务任务数 | 客户端并发 | 后台并发 |
| --- | ---: | ---: | ---: |
| ramp | 10 | 1 | 1 |
| steady_state | 20 | 2 | 1 |
| burst | 40 | 4 | 1 |
| recovery | 10 | 1 | 1 |

合计最多 160 个完整任务；每个任务使用一个新文档和一条要求。分别预先固定 TXT、文本 PDF、DOCX 的原始字节、大小分布、SHA 和业务断言，不使用客户未授权资料。初次受控试验的单文件上限为 1 MiB；大文件、扫描件及 OCR 不在该样本的容量承诺中。

| 必须观测的业务边界 | 测量与断言 | 初始目标（待批准） |
| --- | --- | --- |
| upload | 建立会话、分片 PUT、完成；原文件和恢复读取 SHA 一致 | p95 ≤ 5,000 ms |
| ingestion | 完成上传到 ready；真实 Outbox/Redis/Worker、生成代次和块数量 | p95 ≤ 30,000 ms |
| retrieval | 对授权资料实际检索，记录该阶段耗时及引用来源；区分向量供应商等待 | p95 ≤ 2,000 ms |
| generation_recovery | 接受后台任务、刷新/重新连接、同幂等键重放、主路故障/两路故障；复核和 CSV | 接受请求 p95 ≤ 250 ms；终态不超过冻结的业务截止时间，首轮提案 180 秒 |

各场景、各阶段和每轮分别判定，不能把慢生成混进快健康请求的总体分位数来掩盖失败。正常业务错误率提案 ≤1%，CPU/内存/数据库连接池余量提案 ≥20%。故障注入的预期失败另列，必须满足可见失败、结果保存、次数上限和额度释放；它们仍进入完整执行分母。最终任务延迟不得以返回 `202` 的耗时代替。

旧 readiness 250 ms 目标及其失败记录不变。尚未批准的业务目标不得写成批准后的 `acceptance_objectives`；批准应先于执行，随后绑定完整计划哈希。

## 在不增加常驻监控服务的情况下采样

现有 4C4G 配置中 API、Worker、Consumer 的 CPU limit 合计为 1.8 核，内存 limit 合计为 1,536 MiB；这是仓库配置，执行前还须读回实际 Deployment。旧示例中的 3.25 核/3.25 GiB 分母来自 4C8G，不能沿用。

1. 使用 `collect_business_telemetry.py` 经已知 SSH 主机发送标准库探针，不在远端落地文件。只读 `kubectl get`、containerd 内容索引、`/proc`、根盘及逐个 API/Worker/Consumer Pod 的 `/metrics`，避免 Service 负载均衡漏掉副本。业务节点不增加 Prometheus/Grafana/Alertmanager，不开放公网 metrics。
2. 每次探针完成后至少间隔 5 秒，保留带时间戳的 Pod CPU/内存、进程 RSS、DB 池和依赖观测；同时记录节点可用内存、磁盘和容器重启/OOM。进程 RSS 不能代替整机余量。当前已部署版本的队列/Redis 指标仍不可用于验收；新候选 Worker 已加入真实采集与时间戳，须在获准部署后重新核验。
3. 每阶段至少覆盖一分钟并覆盖完整排队消退过程，使 rate/histogram 有有效采样。空值、NaN、未采到的模型或对象指标保留为缺失，不能填 0。
4. 原始请求样本、阶段开始/结束、队列变化及摘要均绑定到报告；前后采集实际镜像/配置，出现漂移或采样空洞则该次验收不成立。

此路径已在当前主机做只读验证，尚未用于业务负载试验。没有完整遥测时保持待验收，不用低负载快照估算企业数。

### 只读观测命令与报告

以下默认只校验计划，不连接服务器、不创建输出目录；SSH 必须已配置别名和可信主机记录，不接受交互登录或跳过主机校验。

```powershell
.venv\Scripts\python.exe -B -X utf8 -m scripts.collect_business_telemetry `
  --ssh-host enterprise-doc-staging-4c4g --samples 13 --interval-seconds 5 --max-run-seconds 120
```

获得该目标只读观察授权后，显式执行；`taskEvidenceDir` 指向本任务已有的集中恢复组，子目录必须尚不存在。

```powershell
$telemetryOutput = Join-Path $taskEvidenceDir 'business-telemetry-observation-new'
.venv\Scripts\python.exe -B -X utf8 -m scripts.collect_business_telemetry `
  --ssh-host enterprise-doc-staging-4c4g --samples 13 --interval-seconds 5 --max-run-seconds 120 `
  --execute-readonly --output-dir $telemetryOutput
```

本地输出逐次刷新的 `samples.jsonl` 和最终 `run.json`，记录执行源码 SHA、完整样本分母、缺失/过期/漂移、实际镜像/资源限制及配置摘要。单次远端最多 20 秒、本地 SSH 最多 25 秒，剩余不足 25 秒不发下一次探针。故障或中断的样本不补采，后续未运行样本保留在最终报告。输出不含配置明文、Secret、网络地址或原始 metrics 正文；SSH 别名保留在私有计划，不提交原始报告。

OCI 索引摘要、amd64 平台摘要以及 containerd 归档索引摘要可能不同。工具校验内容 SHA、平台和成员关系，记录可验证的对应关系；未证明的摘要仍报告失败。只读探针不证明运行提交、迁移版本或 Secret 版本，这些仍需部署验收补齐。

`--business-report <本地业务 run.json>` 只记录报告哈希和阶段时间重叠，始终 `target_binding_verified=false`。不同机器上时间重叠不能证明同一试验环境。依赖计数差值是进程观测事件，不是本次任务的模型请求数、供应商账单或可直接归属的业务延迟。

观测旧部署仍会因队列/Redis 缺少新鲜度证据返回 `observation_incomplete`、退出码 1。候选部署后，只有每个 Worker 的数值有限、本次成功、最近成功时间在 45 秒内且属于当前进程时，才解除该缺口；全部观测条件齐备可返回 `read_only_observed`、退出码 0。配置拒绝为 2，dry-run 为 0；这些状态均不批准容量，`production_capacity_approved` 始终为 false。

### 2026-09-25 观测范围

- 第二轮 13/13 快照成功，7 个不同 Kubernetes 节点时间戳；无身份漂移、重启或 OOM。物理内存总量 3,904,057,344 字节，窗口内最小可用 1,673,691,136 字节；根盘最小可用 17,670,619,136 字节，CPU 区间最大繁忙约 10.08%。这是一段只读窗口，不能据此承诺业务余量。
- 第三轮仅 2/2 短样本，用于新增归档索引核验：API/Worker/Consumer/Web/Redis 的五组镜像关系均通过，Worker 已证明归档索引→部署索引→amd64 平台的链条。该窗口只有 1 个不同 Kubernetes 时间戳，不能合并冒充更长容量试验。
- 前两轮的镜像未解析结果和各轮原始文件保留，各自绑定当时执行源码。第三轮仍缺少队列/Redis 生产端新鲜度；没有业务负载、模型请求、邮件、远端迁移或部署。

### 队列和 Redis 指标候选

Worker 在启用 metrics 时每轮完成后间隔十秒采集，两来源各限两秒；复用现有 DB/Redis，不新增服务。队列只统计 `pending/retry_wait` 且已到 `available_at` 的任务，从本次可执行时间计算等待年龄，覆盖解析、Agent 和售前任务。未来重试、运行中和终态不属于该指标；它也不替代过期运行租约监测或端到端等待时长。

Redis 新指标为 `enterprise_doc_redis_connected_clients`，来自 `INFO clients` 的服务端连接数。旧 `enterprise_doc_redis_connections` 仍是进程连接数定义，保持未测 NaN，不混用口径。来源为 queue/redis 的 `enterprise_doc_resource_sample_success` 和 `enterprise_doc_resource_last_success_timestamp_seconds` 分别记录本次结果和上次成功时间。初始化、超时、连接失败为未知，不能显示为空闲；失败保留最后成功时间，便于判断持续多久未恢复。

候选已用本机独占 PostgreSQL schema 和实际 Redis 验证积压/消退、连接变化、连接失败恢复和表锁超时，未改变线上。当前应准备具体候选部署与回滚，获准后核对每个 Worker 的新鲜度，再执行受控业务试验。历史只读观察和失败结果保持原样。

## 本地采样器与执行方式

新增 `scripts/run_business_capacity.py`，复用真实上传/库存/Presales API 和 `HybridRetrievalService`，分别记录 upload、ingestion、retrieval、generation_recovery。检索在采样进程运行，不是新的 HTTP 接口，也不是生成内部检索的独立测量。原 `run_application_capacity.py` 继续用于 health/ready/status/Agent，不接受新报告作为商业容量通过证据。

可直接验证仓库内的固定 TXT 示例，无需凭据或网络：

```powershell
.venv\Scripts\python.exe -B -X utf8 -m scripts.run_business_capacity --plan infra/capacity/business-capacity.example.json
```

示例为两轮、共 10 个任务、burst 并发 2，其他阶段并发 1；它是工具验证示例，不是上面的 160 任务容量试验。其固定字节/SHA 随仓库提供；更换文件或复制计划时须同时提供正确文件和哈希。TXT/PDF/DOCX 及三种故障组合由集成测试生成并冻结，采样器不会因 `fault_label` 自动注入故障。

执行前需单独准备本机回环 API、DB、Redis、对象存储、解析消费者和后台生成器，以及一个无文档/响应表的新测试企业。API/消费者必须使用 Hash 向量和受控模型；服务端背景模式须开启。命令行只检查自身配置，`--confirm-controlled-target` 表示操作者已检查目标服务，不是自动证明。正常示例要求返回含固定原文引用的有效草稿；单纯启动默认 deterministic 模型不保证该业务断言成立。

CLI 不读取 `.env` 文件。按 `.env.example` 的键名，通过当前进程环境显式提供本地 `DATABASE__URL`、`REDIS__URL`、`OBJECT_STORE__*`、`AUTH__*`；设 `APP_ENV=local`、`MODEL__PROVIDER=deterministic`、`EMBEDDING__PROVIDER=hash`，并在 `ENTERPRISE_DOC_LOAD_TOKEN` 放入该测试企业 owner 的实际 JWT。不要把凭据放入计划、命令参数、报告或 Git。

```powershell
# taskEvidenceDir 应为当前 CODEX_HOME 集中恢复组的既有目录。
# business-run-01 必须尚不存在；以下命令会写入本地测试企业。
$sampleOutput = Join-Path $taskEvidenceDir 'business-run-01'
.venv\Scripts\python.exe -B -X utf8 -m scripts.run_business_capacity `
  --plan infra/capacity/business-capacity.example.json `
  --execute-local --confirm-controlled-target --output-dir $sampleOutput
```

输出 `run.json`、固定计划 `plan.json` 和逐任务刷新到磁盘的 `samples.jsonl`。已有输出、非回环服务、非 owner、已有文档/响应表和额度不足都拒绝；显式 HTTP 总量包含 quota 预检，不包含观察器 DB/对象读取及后台内部请求。请求慢流、阶段、单任务和整轮都有截止时间。中断保留活动任务及未提交任务；已被服务器接受的任务可能继续运行，CLI 不删除数据或清队列，后续按资源 UUID 检查。

`local_checks_passed` 仅表示业务断言符合预期，不判断提案 p95 是否达标，且 `production_capacity_approved` 始终为 false。双渠道失败仍为业务失败，另记预期断言通过；正常任务失败、超时、未执行不能从分母删除。报告同时提供各轮/阶段/案例摘要、后台接受与终态耗时，未知费用和未报告 token 保持未知。

完整本地验证：

```powershell
.venv\Scripts\python.exe -B -X utf8 -m pytest tests/deployment/test_business_capacity.py -q
# 使用现有本地 PG/MinIO/Redis；临时 schema/测试资源由 harness 精确清理。
.venv\Scripts\python.exe -B -X utf8 -m pytest tests/presales/test_business_capacity_integration.py -q -m integration
```

两种入口分别为 ASGI 传输、真实随机回环端口与独立 CLI 进程。每种覆盖 10 个任务、7 次业务成功、3 次预期双路失败，验证文档扣量、一次生成/消费或释放、同键重放、引用、复核和 CSV。模型 HTTP 受控，不产生真实供应商调用；新客户端刷新不等于真实浏览器/代理网络故障恢复。

## 剩余放行条件

接下来核对部署后的队列/Redis 指标并绑定业务阶段与环境，补充真实浏览器/代理恢复观测，固定服务实际版本与配置；审核目标和窗口后，才进入当前 4C4G 的两轮业务实测。当前业务 CLI 主动限制为本地受控入口，不能直接指向线上，也不能只改 URL 就宣称完成外部验收。实际主机需独立、有界的执行配置和完整资源采样，按实测结果配置企业准入与并发限制。真实供应商长尾、计费和客户代表性另行验收。

运行出现 OOM、数据或租户隔离异常、调用预算耗尽，立即停止提交新任务并保留在途任务及账本状态。不得删除队列或重写失败结果，也不得因试验失败临时提高超时、并发或通过阈值。
