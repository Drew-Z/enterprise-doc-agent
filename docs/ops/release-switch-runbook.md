# 0031 无迁移版本切换

本入口用于已经运行 `20260924_0031`、且新旧应用已经确认数据库兼容的当前 4C4G 环境。旧 `maintenance_guard.py` 的 0027 迁移计划继续保留原限制，不能替换 revision 后重用。

## 本次具体变更

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

本窗口拟用远端目录 `/run/enterprise-doc-release/ops-rc2-switch-20260928`。它包含恢复数据，须遵守当前任务集中备份规则的**本窗口临时副本例外**；历史例外不沿用。批准后才能安装，目录归 root、0700，文件0600，不向仓库 runner 共享凭据。完成后把状态/日志和文件清单收回集中本地，再清理该次创建的精确文件和目录。

## 停服前完成

1. 固定执行器提交及 CI、rc.2 发布清单和源文件哈希；重新读取 Namespace UID、0031、全部资源、主渠道键、任务和上传状态。主机必须有足够空间，四个候选及四个当前镜像都须在缓存中按摘要确认。
2. 若直拉镜像失败，使用既有受限网络 OCI 中转；完整校验字节数/摘要，确认复用的内容仍在目标缓存，再导入。失败时保持服务运行，不把拉取超时消耗在停服窗口内。
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
