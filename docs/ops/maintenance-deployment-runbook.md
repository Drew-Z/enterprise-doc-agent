# 4C4G 零供应商调用的维护部署

本路线把已批准的候选安装到**保持停机**的业务 Deployment，并运行数据库迁移；业务恢复另有门槛。2026-09-27 首次维护在前置资源校验失败，迁移和候选安装未执行，随后已恢复原服务。会话中断导致迁移前停机约 185.8 分钟，超过原批准的 30 分钟。失败记录保持不变，新窗口必须启用下面的独立守卫。

## 固定发布与执行入口

- 部署镜像：`v0.1.45-rc.1` / `1ac873b06f265a415ed128ed55b9f54f81bdf0b9`。
- 兼容回退：`v0.1.45-rc.0` / `93d95a96d83b9ec7ac26897b9287842d0d798923`。两组四组件摘要见 [镜像验证记录](../../.trellis/tasks/09-24-commercial-operations-acceptance/image-release-validation.json)。
- 运行含维护模式实现且 CI 通过的**精确执行器提交**；它与镜像源码分别记录。使用已有 `deploy-staging.yml` 和 `staging` 环境，不新建主机或常驻服务。
- 管理员在获准窗口前将 staging 环境变量 `STAGING_DEPLOYMENT_MODE` 明确设为 `maintenance`，同时传入 `run_smoke=false`。默认或未设置仍为 `standard`；其他值拒绝。保持原来的十个 workflow dispatch 输入，避免超出既有调用契约。
- 维护模式仅支持 `single-node-4c4g`。必须在 dispatch 前完成停写、排空和四业务副本停机；工作流不会替操作者决定何时中断业务。

## 私有执行包

当前集中恢复组内的 `maintenance-preparation-evidence` 保存以下文件，不提交凭据、租户 ID、地址或完整运行配置到 Git：

| 文件 | 用途 |
| --- | --- |
| `live-inventory.private.json`、`live-config.private.json` | 有时间戳的线上只读配置、实例、任务状态和当前额度周期；不含 Secret 内容 |
| `configuration-inputs.private.json`、`bound-template.private.yaml` | 精确模板、已签名镜像摘要及实际配置输入 |
| `candidate-configured.private.yaml` | 完整候选供审阅，**不能整包 apply** |
| `candidate-prerequisites.private.yaml` | 管理员拥有的配置/前置资源，逐项比较后批准；部署服务账号无写权限 |
| `candidate-migration.private.yaml` | 候选 API 镜像执行的数据库与 checkpointer 迁移 |
| `candidate-paused-workloads.private.yaml` | 仅 API、Worker、Consumer、Web 四个 Deployment，全部 `replicas=0` |
| `candidate-resume-workloads.private.yaml` | 后续恢复业务的候选应用清单；仅在独立批准的恢复步骤使用，维护流程不会应用它 |
| `dispatch-inputs.private.json`、`environment-variable-proposal.private.json` | 十个 dispatch 输入、环境变量明确差异和原模式恢复值；尚未应用 |
| `config-diff.private.json`、`quota-proposal.private.json` | 配置与有限额度提案；不延长周期或重置既有计数 |

配置保持当前 GitHub 登录、对象存储、主/备模型渠道以及全部向量配置（version 3）。新增六项为后台生成 false、自动切换 false、每日 dispatch 上限 200、队列超时 900 秒、渠道失败阈值 3、冷却 30 秒。前两项关闭时，后四项不等于启用后台或切换。

当前仅发现一个有效 pilot 额度周期，截止 **2026-09-29 10:57:59 UTC / 北京时间 18:57:59**。提案为该原周期另设 **100 个成功 Agent 任务、100 MiB 文档处理量**；既有售前额度、已消费/预留量和周期保持不变。这是待审核的上限，不是自动配置结果；过期或版本改变时必须重新生成提案。其他企业不得自动获得无限额度。

`configure-products` 必须先预览，核对企业、entitlement ID、expected version 和限制，再用相同参数加 `--execute`。具体无密钥参数已写入私有提案，数据库连接仅由获准执行环境提供。迁移至 0029 及以上前不能执行产品额度配置。

## 开窗前必须完成

1. 选定实际低峰维护窗口、负责人、停止点，并将**后续恢复业务的条件和调用预算一并批准**。维护流程成功仍保持停机；不能先停服再等待恢复方案。流水线 90 分钟和迁移 Job 2700 秒是终止上限，不是停机承诺。
2. 重新读取 namespace UID、部署 profile、数据库版本、四组件镜像、Secret 引用及配置哈希；与执行包比较。Secret 内容不进入记录，轮换必须单独记录。没有批准的差异就停止。
3. 停止外部入口及其他写入方的新提交，完成旧任务。只读检查 jobs、Presales、Agent、工具执行、上传及未释放预留；人工等待不能算排空。首次维护已清理精确 **7 条已过期上传**并释放 2,900 字节，该批次不得重复执行。新增任务/上传须重新盘点。无 HPA/CronJob 仅是历史观察，新窗口重新核对。
4. 保持停写，先等待 Worker/Consumer 完成既有任务，再将四个业务 Deployment 设为零，确认残留 Pod 已消失。保留原副本和版本记录；Redis/PVC 保持原状。
5. 在**同一停写窗口**捕获 DB 和对象的完整恢复点，集中保存并校验 SHA 与数据库引用。使用现有 `backup_database.py --output <本任务恢复组中的新路径> --schema public --record-path <新回执路径>` 及已验证对象捕获流程；不得覆盖既有文件，不向线上桶写快照。9 月 25 日快照和本次只读库存均不能替代开窗前恢复点。
6. 以 rc.0/rc.1 清单核验镜像仍可获取，使用守卫的 `stage` 操作一并修改六项 ConfigMap 字段及对应六项 Namespace 批准注解，随后用完整前置资源校验器读回比对。检查剩余内存/磁盘及配置中的有限额度，再进入迁移。环境变量提案、前置配置和实际渲染结果必须一致；服务账号仍不得读 Secret 或更新前置资源。

## 维护工作流的实际行为

1. 验证 dispatch 组合及目标身份，按固定摘要渲染，并校验管理员前置资源。
2. `staging_maintenance.py prepare` 比较**全部** `EMBEDDING__*` 和向量证据要求；任一新增、缺失或值改变均拒绝。四个业务 Deployment 的 spec/status 副本必须为零，活动 Pod 只允许现有 Redis；其他 Pending/Running/Unknown Pod 均拒绝。未知工作负载或缺少任一应用也拒绝。
3. 生成仅四个零副本应用的工作负载清单。在 scale、删除旧 Job、apply 迁移 Job **之前**，对同机守卫执行一次 `claim`。缺失、心跳超过 10 秒、过期、错误执行器/操作/Namespace、计划或候选摘要不符均拒绝。成功 claim 持久化后不再自动恢复旧版本；迁移失败、取消、超时或旧 Job 未完成时保持人工检查门槛，不能认为数据库未变。
4. 仅迁移成功后应用零副本清单；读回配置、实际镜像和 Pod，确认候选安装且仍停机。Redis、PVC、Secret 不在该维护应用清单中。
5. embedding rollout、应用 readiness 等待、浏览器认证与业务 smoke 均不执行。`always()` 收尾在维护模式下直接退出，不恢复业务副本。
6. 成功记录为 `kind=maintenance-deployment`、`maintenance_status=installed_paused`、`status=blocked_external`；任一必需阶段或停机证明缺失则记录 failed。供应商验证明确为 `not_executed`，不生成普通发布 passed 证据。

以下是维护检查器的 PowerShell 本地文件入口；它只读已有库存和写输出文件，不连接集群。执行器中的库存必须来自该次实时读取，不能手工修改成零：

```powershell
python scripts/staging_maintenance.py prepare `
  --input candidate-configured.private.yaml `
  --live-config live-config.private.json `
  --live-deployments paused-live-deployments.private.json `
  --live-pods paused-live-pods.private.json `
  --workloads candidate-paused-workloads.private.yaml `
  --report maintenance-prepared.json
```

本地测试替代 `kubectl` 进程边界，实际执行 workflow Bash 和 Python CLI；它不代表真实 Kubernetes 迁移或线上供应商计数已验收。

## 恢复或停止

- 迁移前失败：核查数据库仍为原版本和原配置后，依获准恢复步骤恢复原服务。
- 迁移状态不明：维持停机，读取实际版本和 Job 结果。不得自动 downgrade、重放迁移或切换镜像。
- 已到 0031：保留数据库与新历史，选用已验证的 rc.0 兼容镜像或修复后的 rc.1；不能退回 v0.1.44 假定兼容。
- 成功安装后：根据开窗前批准的恢复步骤配置产品额度，应用批准的恢复业务清单，执行真实登录/历史读取/隔离、有限合成业务、浏览器恢复和当前主机容量验收。此阶段可能产生供应商请求，不能继续沿用维护阶段的零调用结论。
- 不要通过重新运行 standard 模式来绕过恢复审阅；它会执行向量 rollout。窗口结束时按记录恢复 `STAGING_DEPLOYMENT_MODE`，读回环境变量和运行状态，留存完整回执。

## 独立守卫的执行包

`scripts/maintenance_guard.py` 与 `scripts/maintenance_guard_cluster.py` 是 Linux 主机上的临时维护工具，不是常驻业务服务。SQLite 文件必须位于专用 runner 与监督器共用的本机文件系统；不得复制成两份或放在 NFS。`BEGIN IMMEDIATE` 将暂停、配置修改、迁移 claim 和恢复串行化；这是可信操作者的并发协调机制，不是抵御管理员绕过工具的权限隔离。

计划 JSON 固定 `schema_version=1`、操作 ID、**新执行器 SHA**、Namespace UID、`original_revision=20260923_0027`、完整渲染候选的 canonical JSON SHA-256、原/候选前置资源、四份带 UID 的原 Deployment spec 和完成 Job 的 name/UID 列表。完整渲染清单经 `Plan.validate_candidate` 验证；原/候选只允许已审核的六个配置新增值及六项批准注解差异，其他资源保持相同。恢复不 apply 模板，不写数据库，不改 Secret/Redis/PVC。

原始基线、计划和执行证据仍存本机既有集中恢复组。下一窗口需新鲜库存和同一停写窗口快照；本轮根据冻结库存生成的 review plan 只用于离线验证，**不能用于 arm**。远端建议临时目录为 `/run/enterprise-doc-maintenance/<operation>`，内容仅限不可变工具、计划的运行副本、SQLite 状态及既有凭据的必要运行引用。复制计划含原配置，须先按用户集中备份规则批准这一临时远端例外；不得静默当成普通缓存。原 `ops-maintenance-2148a8f` 标签保留不覆盖；新执行器的 CI、精确环境标签规则、有人值守的窗口和迁移后恢复步骤须一并审阅。

执行顺序（以下是待激活方案，本轮没有安装或停机）：

1. 核验 Linux `CLOCK_BOOTTIME`、boot ID、systemd、Python/PyYAML/psycopg、kubectl、DB 只读连通性及同机 SQLite 互斥；管理员恢复进程的访问权与部署账号保持分开。目录仅管理员及专用 runner 组可进入，状态文件允许双方写，计划/工具仅管理员可写；DB DSN 只传管理员进程环境，runner 不得读取。未通过就不暂停。
2. 管理员执行 `arm --state <state> --plan <plan> --plan-sha256 <sha> --timeout 1500 --recovery-budget 300`。它在完整原状态/0027 验证后排他创建状态；不能覆盖、续期或重置旧窗口。计时从 arm 开始，因此早于首次暂停。
3. 用 transient systemd service 启动相同工具的 `supervise`，不要使用 `ssh ... &`。单元使用 `Restart=on-failure`、`RestartSec=2s`、`StartLimitIntervalSec=60s`、`StartLimitBurst=3`、`RuntimeMaxSec=35min`、`UMask=0077`；设置审核后的 KUBECONFIG 和 `MAINTENANCE_GUARD_DATABASE_URL` 环境。用 `systemctl show` 核实独立主 PID，再读 `status`，必须看到新鲜心跳才能暂停。它不创建额外 VM，也不发送邮件。
4. 所有停机命令经同一守卫：`pause-web` 关闭公开入口，完成排空检查，再 `pause-backends`；两者均传同一 state/plan/hash。暂停命令只给出停止信号，操作者仍须证明任务排空与 Pod 消失。捕获/核验新恢复点后执行 `stage`。禁止旁路 `kubectl scale/patch`，避免迟到命令在自动恢复后再停服。
5. staging 环境补充 `STAGING_MAINTENANCE_GUARD_STATE`、`STAGING_MAINTENANCE_GUARD_PLAN`、`STAGING_MAINTENANCE_GUARD_PLAN_SHA256`、`STAGING_MAINTENANCE_OPERATION` 四个精确值，保留现有 `STAGING_NAMESPACE_UID` 绑定。源码下载/前置校验必须在 25 分钟内到达 claim；未到达就恢复，禁止重新 arm 延期。
6. 25 分钟截止时，尚未 claim 的窗口变为 `recovering`，永久拒绝迟到部署。监督器在总 30 分钟内核查 UID、原模板、原/候选完整配置、原完成 Job 集合、无新增调度器/未知 Pod、DB 仍为 0027，再用 resourceVersion 条件补丁恢复配置与注解。后端分别就绪后才恢复 Web。部分恢复中进程死亡可以在同一 boot/总预算内重启重新核查；命令明确失败、预算耗尽、主机重启或状态不明则 `blocked`，不能伪报 restored。
7. `migration_claimed` 后监督器退出，自动旧版本恢复永久关闭；原工作流继续受迁移/应用门槛控制。此后无论成功、失败或断线都必须核对实际 DB 和 Job，按 0031 兼容恢复方案处理。30 分钟仅是迁移前恢复预算，集群/数据库不可用时不能保证恢复成功，也不覆盖迁移阶段。
8. 收尾先保存监督器日志、状态、配置/模板/DB/公网只读检查及完整失败证据到集中恢复组，再撤销本窗口四个变量及临时精确标签规则，停止临时单元、核实无进程持有状态，按精确清单删除本轮运行副本。正式执行证据与旧标签保留，商业任务仍不能因此标记通过。

本地验证包括真实 SQLite 并发、启动进程退出后的独立子进程、真实 workflow Bash/Python CLI；集群与 DB 使用边界夹具，主机 systemd/实际恢复时延仍需在正式暂停前做无业务写入的安装检查。没有把本地替代边界的成功标为线上恢复演练通过。
