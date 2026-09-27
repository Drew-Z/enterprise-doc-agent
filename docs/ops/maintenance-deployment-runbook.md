# 4C4G 零供应商调用的维护部署

本路线只把已批准的候选安装到**保持停机**的业务 Deployment，并运行数据库迁移。它不恢复用户流量，也不完成业务验收。2026-09-27 已完成本地准备和线上只读盘点，未开窗、未部署、未迁移、未发送邮件或调用供应商。

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
3. 停止 Web/API、MCP、外部入口及其他写入方的新提交，完成旧任务。只读检查 jobs、Presales、Agent、工具执行、上传及未释放预留；人工等待不能算排空。2026-09-27 的 jobs/Presales/Agent 均无在途，但有 **7 条已过期 active 上传**，未删除或改写；须按现有上传清理流程逐项核对并处理，不能直接改状态凑零。无 HPA/CronJob 仅是本次观察，开窗时须再核对并暂停任何新增调度器。
4. 保持停写，先等待 Worker/Consumer 完成既有任务，再将四个业务 Deployment 设为零，确认残留 Pod 已消失。保留原副本和版本记录；Redis/PVC 保持原状。
5. 在**同一停写窗口**捕获 DB 和对象的完整恢复点，集中保存并校验 SHA 与数据库引用。使用现有 `backup_database.py --output <本任务恢复组中的新路径> --schema public --record-path <新回执路径>` 及已验证对象捕获流程；不得覆盖既有文件，不向线上桶写快照。9 月 25 日快照和本次只读库存均不能替代开窗前恢复点。
6. 以 rc.0/rc.1 清单核验镜像仍可获取，管理员审阅并应用必要前置配置，读回比对。检查剩余内存/磁盘及配置中的有限额度，再进入迁移。环境变量提案、前置配置和实际渲染结果必须一致；服务账号仍不得读 Secret 或更新前置资源。

## 维护工作流的实际行为

1. 验证 dispatch 组合及目标身份，按固定摘要渲染，并校验管理员前置资源。
2. `staging_maintenance.py prepare` 比较**全部** `EMBEDDING__*` 和向量证据要求；任一新增、缺失或值改变均拒绝。四个业务 Deployment 的 spec/status 副本必须为零，活动 Pod 只允许现有 Redis；其他 Pending/Running/Unknown Pod 均拒绝。未知工作负载或缺少任一应用也拒绝。
3. 生成仅四个零副本应用的工作负载清单。执行迁移 Job；失败、取消、超时、旧 Job 未完成时停止，不能认为数据库未变。
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
