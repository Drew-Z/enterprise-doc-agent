# 受监督的无迁移版本切换

本入口不执行数据库迁移。0031/0032的既有发布模式保留；2026-10-08本地候选新增精确0034的镜像切换。旧 `maintenance_guard.py` 的0027和0031→0032迁移计划保留原限制，不能替换revision后重用。下面9月28日rc.2内容是历史窗口记录。

## 0034候选的发布与回退边界（尚未上线）

人工修订和执行档位需要新增0033/0034两列。现网最后观测仍为0032，必须先完成独立受监督的0032→0034迁移及数据库核验；本地迁移入口见下一节，现场执行包仍须绑定新鲜目标。不能以修改计划的`original_revision`冒充迁移成功。

迁移完成后，`scripts.release_switch`仅接受`original_revision=20261008_0034`及显式`release_kind=images_only`。ConfigMap、凭据、连接池、并发和资源规格保持，候选/回退镜像均按固定摘要批准。每次集群检查继续核对实际0034；0032、0033或未知revision均在写入前拒绝。该入口不能回退数据库列或删除新历史。

应用回退必须先排空持久任务和预约。在任何回退写入前检查业务空闲，关闭Web及所有后端后再次检查；若有任务在两次检查之间受理，保持候选模板并拒绝启动旧Worker，不能让不认识`execution_policy`的旧代码领取新策略任务。第二次拒绝可能保持应用暂停，需按原候选恢复并处理在途工作；不得标为已恢复，也不自动重置期限/计量。现有独立600秒执行、300秒恢复、资源身份/版本围栏和Web最后启动保持。

本地证据覆盖192项发布执行器检查及真实隔离PostgreSQL上的rc.40 GET/行投影兼容：新旧档位数据、两次人工修订、权限隔离和历史不变。冻结旧方法/严格schema用于发现新列泄漏到旧解析器的问题；它不替代原签名镜像实际启动、真实迁移/回退测量或业务UAT。当前完整候选验收仍开放。

## 独立0032→0034扩展窗口（本地候选）

使用专用`scripts.presales_schema_expand`，而不是旧维护计划或无迁移镜像切换入口。
计划`schema_version=4`、`release_kind=presales_schema_expand`，固定原0032/目标0034及
`migration_sha256`。原四镜像、完整Deployment、ConfigMap、Namespace批准和凭据必须
完全保持；禁止额外计划字段、任意SQL、池/并发/镜像顺带变更。七份执行器源文件摘要必须
一起固定：新模块及原六份`release_switch.EXECUTOR_FILES`。私有计划/恢复点仍放本任务集中恢复组。

```powershell
.\.venv\Scripts\python.exe -B -m scripts.presales_schema_expand validate `
  --plan <集中目录中的私有计划> --plan-sha256 <计划SHA256>
```

`validate`只校验计划与本地执行器，不连数据库/集群。Linux主机上的`arm`、`execute`、
`status`沿用同样的计划参数并增加`--state <本窗口状态文件>`；Windows通过PowerShell/SSH
传入参数，不调用WSL。`status`也核验状态绑定的目标和计划，旧状态路径不复用。

现场arm前须完成原五工作负载缓存、磁盘余量/完整峰值、目标身份与业务排空检查，以及
原签名rc.40镜像在0034上的运行验证。本地冻结GET测试不足以替代该运行检查。
远端仅使用已批准的本窗口私有运行目录，0700/0600；数据库凭据从精确Secret读取到libpq
环境，不放命令行或日志。需要`psql`及原执行器的Python依赖，不执行备份功能。

`arm`在数据库锁下验证完整0032与原服务空闲，再创建固定状态。`execute`须由独立systemd
服务执行，`Type=exec`、`KillMode=control-group`、`Restart=on-failure`、
`RestartPreventExitStatus=1`、`RestartSec=2`、`RuntimeMaxSec=930`、`TimeoutStopSec=10`、
`UMask=0077`、`PrivateTmp=yes`、`ProtectSystem=strict`，仅本次状态目录可写。
外部930秒只为进程退出保留余量，内部仍严格限制arm后600秒执行和最多300秒恢复；
重启不延长数据库/集群操作期限。主机boot变化或状态过期拒绝继续。

执行顺序为：取得数据库会话advisory lock → 验证0032/结构/空闲 → 关闭Web和全部后端并
确认Pod退出 → 再查空闲 → 同一事务锁定版本与两张目标表，执行固定0033/0034 SQL →
验证完整0034列/对象约束 → 后端逐个就绪后最后启动Web → 清除本窗口围栏。
两份DDL和版本更新同事务，不提交0033中间态。原计划所有资源保持，不创建/删除迁移Job。

数据库回执丢失不代表未提交。失败路径关闭原psql会话；恢复必须重新取得同一会话级锁后，
才能读取完整0032/0034并改变集群。锁不可用、列/约束与revision不一致、在途业务或资源
漂移时记录blocked，不能自动降级列、清任务或恢复未知状态。psql语句/锁等待和输出均有界，
原始数据库错误不写日志。恢复成功只表示原应用恢复；`restored`仍非零退出，不能算迁移成功。
确认新鲜0034后，另行构建E1镜像切换计划；本窗口不部署新功能或调用模型。

本地验证包括真实数据库的原子失败、提交前/后回执丢失、锁竞争、结构漂移和业务拒绝；
受控Kubernetes边界验证停服/恢复顺序。实际主机迁移、原签名镜像启动及发布回退测量仍待完成。

## 历史rc.2窗口具体变更

- 应用镜像由 rc.1 切换到 rc.2 的四个固定摘要，见[候选与回退表](online-acceptance-next-window.md)。数据库始终为 0031。
- 主渠道使用本地配置中的 `grok-4.7`，备用 `grok-4.6` 保留；售前选路改为 primary。
- 后台生成与有界自动切换启用，后台并发为 1；主模型等待上限 120 秒，原售前 120/150 秒及其他请求上限保留。
- 不提交业务任务，不执行模型/向量 smoke，不延长原 pilot。服务就绪与真实业务验收分别报告。

## 计划和恢复点

集中本地恢复组保存新鲜库存、当前 ConfigMap/Namespace/四份 Deployment，以及主渠道旧/新密钥。私有计划只携带 `MODEL__API_KEY` 两个值；其他 Secret 数据仅保存哈希，执行时从现有 Secret 读取数据库连接到进程内存。

`ReleasePlan` 校验全部前置资源、两组镜像、配置哈希、主渠道批准字段、单键凭据差异以及其余配置不变。`executor_sources` 必须固定六个执行模块的 SHA-256；计划本身也绑定 SHA-256。只校验计划不会连接集群。

```powershell
.\.venv\Scripts\python.exe -B -m scripts.release_switch validate `
  --plan <集中目录中的私有计划> --plan-sha256 <计划SHA256>
```

本次使用的远端目录为 `/run/enterprise-doc-release/ops-rc2-switch-20260928`。用户已批准该窗口的临时副本例外，实际安装为 root、0700/0600，未向仓库 runner 共享凭据。成功后状态/日志及文件清单已收回集中本地，11 个文件、该目录和独立服务已清理。该窗口例外已结束；下一窗口必须另设操作标识与具体例外，不能复用旧状态。

## 停服前完成

### 当前镜像预热入口（2026-10-08）

rc.41预热造成DiskPressure后，后续导入必须使用同一版本的
`scripts/import_staging_oci_archive.py`与同目录的`image_cache_safety.py`。
中转回执新增`receiver_dependency`及`receiver_batch_option`，两份代码应一起传输。
将本次全部API/Worker/Consumer/Web完整OCI归档写进一份`--batch-plan`，不要沿用
只含缺失blob的历史传输器。计划格式为`schema_version: 1`及`archives`数组；每项
包含`archive`、`expected_sha256`、`base_name`和可选`image_reference`，归档相对路径
相对于计划文件。只读计划不连接集群，也不证明现场空间充足。

在Linux主机上以参数列表运行（由PowerShell的SSH入口传入）：

```text
python3 -B import_staging_oci_archive.py --batch-plan /approved/batch.json
python3 -B import_staging_oci_archive.py --batch-plan /approved/batch.json --confirm --record-path /approved/result.json
```

确认导入时，先核对全部压缩内容、解压快照、规范化临时副本与元数据峰值；按实际
node/cache/temp文件系统分别保留有效驱逐阈值和minimum reclaim，以及inode余量。
原五个工作负载（含Redis及init容器）的摘要、完整/已解包内容、CRI别名必须可用。
创建临时归档前及每次导入前重新观测，任何原模板、节点、boot、政策或空间漂移均拒绝；
整批共用600秒截止，不因超时重试。末态再核对原缓存和余量，失败不自动清理镜像或停服。

当前约40GB根盘的硬阈值与minimum reclaim均10%；约4.87GB可用低于两者约8.43GB
保留量，本次只读检查应拒绝新预热。该数值是本次观察，未来执行必须重新测量。
历史缓存删除仍需对应精确计划批准；本地守卫通过不表示rc.41已发布或总验收通过。

### 既有发布步骤

1. 固定执行器提交及 CI、rc.2 发布清单和源文件哈希；重新读取 Namespace UID、0031、全部资源、主渠道键、任务和上传状态。主机必须有足够空间，四个候选及四个当前镜像都须在缓存中按摘要确认。
2. 若直拉镜像失败，使用既有受限网络 OCI 中转；完整校验字节数/摘要，确认复用的内容仍在目标缓存，再导入。失败时保持服务运行，不把拉取超时消耗在停服窗口内。

   镜像列名与 CRI ImageStatus 成功都不足以证明可启动。必须核对 CRI `repoDigests` 中本次导入别名也真实存在于 containerd image store，并与获准摘要一致；详见[缓存运行时契约](../../.trellis/spec/backend/image-cache-runtime.md)。本次初始预检查漏掉此项，导致 API CreateContainerError；原失败保留。
3. 用正式渲染器复现候选；同步准备 GitHub staging Environment 的模型地址/名称、后台/切换、`STAGING_MODEL_TIMEOUT_SECONDS=120` 与 `STAGING_PRESALES_CONCURRENT_ATTEMPT_LIMIT=1`。这两个新可选输入保持十个 workflow_dispatch 参数。发布前后需读回变量，回退恢复原值。
4. 在独立 systemd 进程下验证 Python/PyYAML、kubectl、psql、只读数据库及权限；不启动迁移 Job。临时运行包和独立服务未经本窗口确认时不安装、不 arm。

## 独立执行

以下是 Linux 服务器参数约定，由本地 PowerShell/SSH 控制器传递参数列表，不在 Windows 调用裸 bash。

- `python -B -m scripts.release_switch arm --plan ... --plan-sha256 ... --state ...`：核验原部署和业务空闲后创建新状态，重复路径拒绝。
- `python -B -m scripts.release_switch execute --plan ... --plan-sha256 ... --state ...`：必须由独立 systemd 服务运行。使用 `Type=exec`、`KillMode=control-group`、`Restart=on-failure`、`RestartPreventExitStatus=1`、`RestartSec=2`、`RuntimeMaxSec=660`、`TimeoutStopSec=10`、`UMask=0077`、`PrivateTmp=yes`、`ProtectSystem=strict`；只给本次 state 目录写权限。服务不得随 SSH 会话退出而终止。
- 切换期限固定为 arm 后 600 秒，最多另有 300 秒恢复预算。进程重启不能延期；主机重启、状态丢失或原状态已过期均拒绝继续。
- 唯一持锁执行器先保存 `applying` 再关闭 Web 和后端。业务 Pod 全部退出并再次只读核对无在途后，统一切换 ConfigMap、Namespace、主渠道键和四份模板；后端全部就绪后最后开放 Web。
- 检查最终配置/键/模板，再清理临时围栏标记，重新运行正常前置校验。只有这些步骤成功才记录 `succeeded`。

`succeeded` 表示部署与资源校验成功，**不是正式商用或真实供应商验收通过**。计划构建、Linux 进程实验和模拟 Kubernetes 边界也不能代替主机实际切换。

## 失败处理

进程在 `applying` 中断时，新进程只进入 `recovering`，不再次发布。恢复先停止业务进程，恢复整组配置/凭据/模板，再按后端→Web 顺序启动。每次更新包含 UID/resourceVersion 条件和真正改变的临时围栏，避免迟到请求以旧版本覆盖恢复结果；清理标记后正常部署校验仍可运行。

未开始便超时的 armed 操作不暂停健康服务。当前进程确认预检查在任何写入前失败（例如用户刚提交业务）时，记录 `preflight_rejected`，不启动停服回退；进程已经死亡而无法确认写入情况时仍按未完成操作恢复。目标、模板、其他凭据、调度器或数据库版本漂移，以及恢复预算耗尽均记录明确失败，不覆盖未知变更、不自动重试业务、不清库/清队列。`restored` 返回非零，不能把回退成功算作发布成功；`blocked` 需要人工按集中恢复点核对，不能声称自动恢复或 RTO 已达标。

## 本次执行结果

2026-09-28，执行器 `edb0551` 的真实 systemd 预检查通过后，arm 到 succeeded 为 431.150 秒，服务未重启、期限未重置。中途核实 CRI 的四个 `import-2026-09-28` 引用缺少实际别名，按同一批准摘要补齐，当前执行继续完成。API→Worker→Consumer→Web 依次就绪，五个 Deployment、四份完整候选模板、配置/批准信息和主渠道键读回通过，临时围栏全部移除。

公网首页与带依赖/时间校验的 readiness 通过（使用本机已配置代理）。成功后 11 项 GitHub staging 变量同步并读回；运行与回退需要的镜像别名保留。未执行新业务、模型/向量请求、迁移或测试邮件，也未演练 rc.2 回退。生产浏览器、容量、供应商质量和商业门槛仍待验收，详见[脱敏执行记录](../../.trellis/tasks/09-24-commercial-operations-acceptance/release-switch-execution-validation.json)。
