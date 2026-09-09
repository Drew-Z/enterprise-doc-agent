# 持续任务交付与下一次 staging 验收计划

日期：2026-09-09，Asia/Shanghai。状态：本地计划待确认；代码尚未提交，下一次发布版本与镜像尚未创建。

两个代码子任务已完成本地验收，主任务继续保持进行中。当前规范目录为
`D:\workspace4Cursor\enterprise-doc-agent`，分支 `main`，HEAD 为
`ffca3d0861ae22c857fef54260b9fc4c083e980e`。工作树包含旧的 86 文件故障归档，
以及本轮两个修复和持续任务记录。暂存区为空。

## 已有验证

| 交付内容 | 实际验证 | 记录 |
|---|---|---|
| smoke 成功/失败 JSON | 120 项定向及相关测试；当时全量 1077 项非集成通过 | [子任务 1](../09-09-staging-smoke-failure-evidence/validation.json) |
| consumer 实例与 attempt 关联 | 51 项 Worker 定向、1 项真实注册适配器、21 项 Agent/Job 本地集成通过 | [子任务 2](../09-09-consumer-attempt-attribution/validation.json) |
| 两个修复合并后的本地源码 | 1086 项非集成通过，127 项集成被排除；mypy 161 个源文件；Ruff lint/format 通过 | [子任务 2 最终门槛](../09-09-consumer-attempt-attribution/validation.json) |

代码检查使用仓库现有 Python 3.12 环境及 `-X utf8 -B`。测试、mypy、Ruff 缓存使用
自动清理的系统临时目录。本地组合集成直接调用真实注册的 Celery adapter、读取 loopback
PostgreSQL，并禁止 gateway/MCP 调用；它不代表 Redis broker 投递或 staging 已验收。

原 86 文件、第一子任务源码、HEAD 和真实 Git index 已核验无漂移。最终本地计划检查见
[validation.json](validation.json)。历史 v0.1.34 候选仍未验收，历史 Agent 失败根因仍未确定；
不把诊断缺失填成零调用、零费用或已知原因。

旧 workflow 归档中四个名为 `.json` 的文件是合并 stdout/stderr 的原始命令输出：
三份是 K3s 警告加结构化载荷，一份是警告加 NotFound。对应 collection-status 退出码已核对；
它们保持原字节，不作为本轮新写的 JSON 记录重新序列化。验收记录分别列明这些原始捕获与
可直接解析的机器记录。

## 本次请求确认的本地提交

| 顺序 | 提交消息 | 文件数 |
|---|---|---|
| 1 | `docs: record v0.1.34 deployment failures and diagnostics` | 86 |
| 2 | `fix: retain staging smoke failure reports` | 10 |
| 3 | `fix: correlate consumer runtimes with durable attempts` | 16 |
| 4 | `docs: maintain continuous Trellis delivery plan` | 16 |

合计 128 个文件。下方列出全部准确路径；机器可读清单为
[commit-manifest.json](commit-manifest.json)。旧故障归档独立为第一批，来源已通过原始
快照与逐文件哈希确认。当前没有未识别且被悄悄纳入的文件；执行前若出现新路径或已审阅
源码/归档哈希变化，停止并重新核对受影响范围。

本次确认仅覆盖上述四个工作提交。执行时逐批 `git add` 准确文件列表并创建新提交，
保持既有历史，不 amend、不推送。正式 Trellis archive 与 journal bookkeeping 在工作提交
之后按现有流程记录，本计划没有提前执行或宣称完成它们。

规则来源是 [Trellis Phase 3.4](../../workflow.md#34-commit-changes-required--once)：
“Present the plan once, ask for one-shot confirmation”；该步骤还规定 “Do not amend. Do not push.”
工作区 [AGENTS.md](../../../../AGENTS.md) 同样要求没有明确请求时不自动推送。

## 后续发布依赖

这部分定义后续工作的门槛，不是当前可直接执行的部署批准。下一版本号、最终源码 SHA、
四镜像 digest、现场前后值及运行记录都必须在实际取得后填写。

1. 四个本地工作提交完成后，记录实际 commit/tree 和剩余状态。正式 archive/journal 按
   Trellis 顺序收尾，重新核对将要发布的最终源码；当前已有源码哈希必须仍匹配。
2. 获得推送范围后，先回读远端默认分支，正常处理必要的合并及验证；不强推。核验最终
   发布源码的 GitHub Quality 结果。自动 Quality 仅覆盖其真实 backend/frontend 门槛，
   本地 22 项集成结果保留独立来源。
3. 审阅并确定一个新候选 tag 及其准确源码。旧 `v0.1.34` 不包含本次修改，不改写旧 tag。
   tag 推送会触发 [Container Supply Chain](../../../.github/workflows/container.yml)，
   包含 registry 写入、四镜像构建、扫描、SBOM、provenance、签名及验证；须明确授权后执行。
4. 检查严格 release manifest，要求 api/worker/consumer/web 四项全部成功，同一源码，
   OCI index 与部署平台 digest 可核对，签名/attestation 的仓库、workflow、commit、digest
   绑定完整。保留原始 artifact 哈希。扫描结论受既有 severity/ignore-unfixed 范围约束。
5. 使用该候选源码的 [Deploy Staging](../../../.github/workflows/deploy-staging.yml) 和
   同一候选的四个 digest 形成具体部署表。工作流会从自身 ref 下载 smoke 脚本，所以只替换
   consumer 镜像而沿用旧 workflow ref 不能验收新的失败报告。
6. 在实际部署前，重新只读确认当前 runner/Namespace UID、Kubernetes context/API server、
   deployment profile、Pod UID/启动时间/镜像、已完成或失败的固定名 Job、18 项前置资源及
   17 项 approval annotation、受保护变量和认证有效期。2026-09-08T17:27Z 的历史快照
   只作基线，不代表当前现场。
7. 为新 digest 渲染清单，确定 administrator-owned image allowlist、prerequisite 指纹及
   回滚变量的准确前后值。任何修改使用 UID/旧值前置条件和 dry-run，待整体方案获批后执行。
   不沿用旧 v0.1.34 的补丁值。
8. 回滚候选以最近已验收的 v0.1.33 记录为起点，重新核对实际 revision/digest、缓存和
   数据库兼容性。旧 revision 数不是新的执行输入。实际 rollback 另有具体批准。
9. 复核同一合成 owner/member 的 session/role/capabilities。三份短期 smoke Secret 的
   有效期必须覆盖 90 分钟工作流及余量；必要刷新纳入具体部署授权，仅经内存/stdin，
   不输出或归档令牌，不更改成员。
10. 用同源代码执行只读 reindex 计划；selected 非零时先审阅重建范围。当前采用
    `single-node-4c4g`，不扩大资源、模型路由、超时或网络权限。

## 下一次部署需一次写明的影响范围

经批准后，只启动一个新的 Deploy Staging run，并固定 ref、四 digest、十个 dispatch 输入、
受保护变量快照和 `run_smoke=true`，保持已启用的治理 smoke。记录 run ID/attempt 及所有步骤
实际 outcome，不自动 rerun。

- 迁移阶段会把 API/Worker/Consumer/Web 缩至 0；成功后应用候选 workloads。
- embedding gate 期间 API/Web 暂停、Consumer 暂时为 4 副本，随后恢复候选清单。流程中的
  `always()` 恢复应用的是本次候选，不是回滚到上一验收版本。
- 工作流会替换已完成的固定名 migration/embedding Job；遇到未完成的 Job 会保留并失败。
  若确需删除失败 Job，先形成包含实际 UID、状态、恢复边界的单独具体范围。
- 既定 embedding probe 为两个合成输入；业务 smoke 为一份上传和一例 QA；治理 smoke
  另上传一份并执行 ACL、retention/legal-hold、identity-binding 检查。可能发生 embedding、
  模型修复或 fallback 调用。记录实际调用/使用量，可观测性不足的费用保持未知。
- reindex 在预检之后仍可能因数据变化选中文档；保存实际 selected/created/replayed/apply
  数量。案例数、批次和时限不等于金额上限。合成数据按现有流程保留。
- 不包含新的 12/40 例 RAG trial、现场故障注入、镜像 relay、清理旧数据或独立恢复演练。

## 成功与失败的验收材料

| 门槛 | 必需事实 | 失败或缺失时 |
|---|---|---|
| 源码与运行实例 | 最终源码/tag、四镜像 digest、Pod UID/启动时间/运行 imageID、consumer 配置事件的 worker_id/PID/hostname | 记录缺失，不能用后来启动的导入探针证明历史执行归属 |
| 平台步骤 | prerequisites、migration、workloads、rollout、embedding、readiness 均实际成功 | 保留原 outcome，后续 Ready 不补记已失败或跳过的步骤 |
| 业务成功 | smoke schema_version=2、status=passed、sample_count=1，已完成所有步骤，artifact 下载/SHA-256/citation 校验通过 | 业务 gate 失败；sample_count 仅为确认创建的 Agent 数 |
| 业务失败 | 有效 CLI 执行后取得 failed JSON、已完成步骤、failure.step/code/http_status、真实已取得 ID 的哈希、canonical terminal status；退出码仍非零 | 进程强杀、无效 argparse 或文件不可写等限制单独记录；stdout/原 workflow 日志保留 |
| 执行归属 | smoke 的 agent_run_id 哈希，经只读 execution/job/attempt 关联到 worker_id、job_ref、attempt_ref 和真实 claim 事件 | 缺失关联保持 unresolved，不能猜测另一个消费者或 provider 根因 |
| 自然 handler 失败 | 对实际经过该路径的失败，保留 Agent 安全诊断事件与成功落库后的 failure-recorded 事件，并与 durable attempt 比对 | settlement 抛错或日志 sink 失败允许没有事件；不伪造“已记录”，也不为补日志额外运行 QA |
| 治理与发布 | 已启用的治理 smoke 成功，严格发布 record、脱敏 artifacts 与 manifest 哈希完整 | 治理 skipped/failed 或必需证据缺失时发布不通过 |

成功 Agent 不要求出现失败事件。Claim 事件说明成功 claim，执行完成仍由 durable 状态和业务
smoke 证明。现有 collector 在 `always()` 中收集 smoke JSON、current/previous Worker 与
Consumer 日志及发布记录，但每容器只有 tail=500；应及时按实际 Pod UID 和 smoke 时间窗
补齐有界只读收集并记录退出码。已被移除或截断的日志明确记为不可取得，不用新进程观测替代。
[日志规范](../../spec/backend/logging-guidelines.md) 规定哈希关联字段，
[smoke 规范](../../spec/backend/cicd-kubernetes.md) 规定失败报告。现有脱敏器保留这些关联哈希，
但禁止原始业务 ID、payload、签名 URL、凭据和异常正文进入新增事件或公开归档。

下载本次 workflow artifact 后，校验 ZIP/内部 manifest/每文件 hash 与 size，按新 run/attempt
使用唯一目录保留原字节，维持 evidence 的换行保护。失败也形成失败 record；不覆盖历史文件，
不把候选 workloads 恢复写成回滚，不把缺失 provider usage 改成零。

## 持续任务切换与保留项

本地计划通过后，本子任务进入 review，主任务的当前阶段为待交付确认；下一步执行被批准的
准确本地提交。之后规划候选源码/供应链审阅和真实 staging 验收。M5/M7 的代表语料、独立语义
复核、模型版本/计费信息和重复性门槛，以及 M6 独立故障域恢复仍保持开放。

本轮已确认的测试临时目录均由 TemporaryDirectory 清理。保留旧部署临时根
`C:\Users\zhang\AppData\Local\Temp\eda-v0.1.34-deploy-01a080bd`、
旧 review 目录 `C:\Users\zhang\AppData\Local\Temp\eda-v0.1.34-review-5__gd4f6`、
未合并的 actions-minutes worktree、有既有修改的 admin-v014 worktree、旧 prunable 登记及
归属不确定的缓存。未重试先前被策略拒绝的 13 文件清理。详细保留边界见
[历史部署计划](../../../docs/ops/v0.1.34-staging-deployment-plan.md#临时材料保留)。

## 准确文件清单

### 1. docs: record v0.1.34 deployment failures and diagnostics

共 86 个文件。

```text
.gitattributes
.trellis/tasks/07-19-m5-observability-eval-load/implement.md
.trellis/tasks/07-19-m5-observability-eval-load/research/20260909-v0.1.34-smoke-diagnosis.md
docs/ops/NEXT_STAGE_PLAN.md
docs/ops/v0.1.34-staging-deployment-plan.md
evidence/index.json
evidence/m6/20260908-v0.1.34-staging-deployment-failure.json
evidence/m6/20260908-v0.1.34-staging-release/artifact-download.json
evidence/m6/20260908-v0.1.34-staging-release/artifact-inventory.json
evidence/m6/20260908-v0.1.34-staging-release/artifact-verification.json
evidence/m6/20260908-v0.1.34-staging-release/deployment-terminal.json
evidence/m6/20260908-v0.1.34-staging-release/final-postflight.json
evidence/m6/20260908-v0.1.34-staging-release/late-recovery-events.json
evidence/m6/20260908-v0.1.34-staging-release/migration-failure-live-observation.json
evidence/m6/20260908-v0.1.34-staging-release/migration-pull-observation-1255.json
evidence/m6/20260908-v0.1.34-staging-release/migration-pull-observation-1302.json
evidence/m6/20260908-v0.1.34-staging-release/postflight.json
evidence/m6/20260908-v0.1.34-staging-release/preflight.json
evidence/m6/20260908-v0.1.34-staging-release/preparation.json
evidence/m6/20260908-v0.1.34-staging-release/redeploy-read-only-preflight.json
evidence/m6/20260908-v0.1.34-staging-release/restore-pull-observation.json
evidence/m6/20260908-v0.1.34-staging-release/rollback-read-only-preflight.json
evidence/m6/20260908-v0.1.34-staging-release/token-rotation.json
evidence/m6/20260908-v0.1.34-staging-release/workflow-failure-lines.json
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-evidence-b0dde98f69f6ed9560c3c018bff2f1b4371283d5.manifest.json
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-release-b0dde98f69f6ed9560c3c018bff2f1b4371283d5.record.json
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/collection-status.txt
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/consumer-current.log
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/consumer-previous.log
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/deployment-profile.txt
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/deployments.json
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/embedding-rollout-job.json
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/embedding-rollout.log
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/events.txt
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/ingress.yaml
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/migration.log
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/network-policy.yaml
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/staging-rendered-staging-embedding-rollout.yaml
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/staging-rendered-staging-migration.yaml
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/staging-rendered-staging-prerequisites.yaml
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/staging-rendered-staging-workloads.yaml
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/staging-rendered-staging.yaml
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/worker-current.log
evidence/m6/20260908-v0.1.34-staging-release/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/worker-previous.log
evidence/m6/20260909-v0.1.34-redeployment-archive-validation.json
evidence/m6/20260909-v0.1.34-staging-redeployment-failure.json
evidence/m6/20260909-v0.1.34-staging-redeployment/redeploy-agent-diagnosis.json
evidence/m6/20260909-v0.1.34-staging-redeployment/redeploy-artifact-download.json
evidence/m6/20260909-v0.1.34-staging-redeployment/redeploy-artifact-inventory.json
evidence/m6/20260909-v0.1.34-staging-redeployment/redeploy-artifact-verification.json
evidence/m6/20260909-v0.1.34-staging-redeployment/redeploy-authorization.json
evidence/m6/20260909-v0.1.34-staging-redeployment/redeploy-cleanup-intent.json
evidence/m6/20260909-v0.1.34-staging-redeployment/redeploy-cleanup.json
evidence/m6/20260909-v0.1.34-staging-redeployment/redeploy-deployment-terminal.json
evidence/m6/20260909-v0.1.34-staging-redeployment/redeploy-dispatch-accepted.json
evidence/m6/20260909-v0.1.34-staging-redeployment/redeploy-dispatch-intent.json
evidence/m6/20260909-v0.1.34-staging-redeployment/redeploy-migration-job-before.json
evidence/m6/20260909-v0.1.34-staging-redeployment/redeploy-postflight.json
evidence/m6/20260909-v0.1.34-staging-redeployment/redeploy-preflight.json
evidence/m6/20260909-v0.1.34-staging-redeployment/redeploy-run.json
evidence/m6/20260909-v0.1.34-staging-redeployment/redeploy-smoke-failure-log.json
evidence/m6/20260909-v0.1.34-staging-redeployment/resume-20260909-checkpoint-target.json
evidence/m6/20260909-v0.1.34-staging-redeployment/resume-20260909-node-inventory.json
evidence/m6/20260909-v0.1.34-staging-redeployment/resume-20260909-postflight.json
evidence/m6/20260909-v0.1.34-staging-redeployment/resume-20260909-runtime-attribution.json
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-evidence-b0dde98f69f6ed9560c3c018bff2f1b4371283d5.manifest.json
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-release-b0dde98f69f6ed9560c3c018bff2f1b4371283d5.record.json
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/collection-status.txt
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/consumer-current.log
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/consumer-previous.log
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/deployment-profile.txt
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/deployments.json
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/embedding-rollout-job.json
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/embedding-rollout.log
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/events.txt
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/ingress.yaml
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/migration.log
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/network-policy.yaml
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/staging-embedding-rollout.json
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/staging-rendered-staging-embedding-rollout.yaml
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/staging-rendered-staging-migration.yaml
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/staging-rendered-staging-prerequisites.yaml
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/staging-rendered-staging-workloads.yaml
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/staging-rendered-staging.yaml
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/worker-current.log
evidence/m6/20260909-v0.1.34-staging-redeployment/workflow/staging-sanitized-b0dde98f69f6ed9560c3c018bff2f1b4371283d5/worker-previous.log
```

### 2. fix: retain staging smoke failure reports

共 10 个文件。

```text
.trellis/spec/backend/cicd-kubernetes.md
.trellis/tasks/09-09-staging-smoke-failure-evidence/check.jsonl
.trellis/tasks/09-09-staging-smoke-failure-evidence/design.md
.trellis/tasks/09-09-staging-smoke-failure-evidence/implement.jsonl
.trellis/tasks/09-09-staging-smoke-failure-evidence/implement.md
.trellis/tasks/09-09-staging-smoke-failure-evidence/prd.md
.trellis/tasks/09-09-staging-smoke-failure-evidence/task.json
.trellis/tasks/09-09-staging-smoke-failure-evidence/validation.json
scripts/staging_smoke.py
tests/deployment/test_staging_smoke.py
```

### 3. fix: correlate consumer runtimes with durable attempts

共 16 个文件。

```text
.trellis/spec/backend/logging-guidelines.md
.trellis/tasks/09-09-consumer-attempt-attribution/check.jsonl
.trellis/tasks/09-09-consumer-attempt-attribution/design.md
.trellis/tasks/09-09-consumer-attempt-attribution/implement.jsonl
.trellis/tasks/09-09-consumer-attempt-attribution/implement.md
.trellis/tasks/09-09-consumer-attempt-attribution/prd.md
.trellis/tasks/09-09-consumer-attempt-attribution/task.json
.trellis/tasks/09-09-consumer-attempt-attribution/validation.json
apps/worker/src/enterprise_doc_worker/agent_handler.py
apps/worker/src/enterprise_doc_worker/config.py
apps/worker/src/enterprise_doc_worker/consumer_main.py
apps/worker/src/enterprise_doc_worker/queue.py
apps/worker/tests/test_agent_handler.py
apps/worker/tests/test_consumer_main.py
apps/worker/tests/test_queue.py
tests/agent/test_consumer_attempt_attribution_integration.py
```

### 4. docs: maintain continuous Trellis delivery plan

共 16 个文件。

```text
.trellis/tasks/07-17-enterprise-document-agent-platform/design.md
.trellis/tasks/07-17-enterprise-document-agent-platform/implement.md
.trellis/tasks/07-17-enterprise-document-agent-platform/prd.md
.trellis/tasks/07-17-enterprise-document-agent-platform/research/20260909-continuous-delivery.md
.trellis/tasks/07-17-enterprise-document-agent-platform/task.json
.trellis/tasks/07-19-m5-observability-eval-load/task.json
.trellis/tasks/07-19-m6-cicd-kubernetes/task.json
.trellis/tasks/09-09-delivery-staging-acceptance-plan/check.jsonl
.trellis/tasks/09-09-delivery-staging-acceptance-plan/commit-manifest.json
.trellis/tasks/09-09-delivery-staging-acceptance-plan/delivery-plan.md
.trellis/tasks/09-09-delivery-staging-acceptance-plan/design.md
.trellis/tasks/09-09-delivery-staging-acceptance-plan/implement.jsonl
.trellis/tasks/09-09-delivery-staging-acceptance-plan/implement.md
.trellis/tasks/09-09-delivery-staging-acceptance-plan/prd.md
.trellis/tasks/09-09-delivery-staging-acceptance-plan/task.json
.trellis/tasks/09-09-delivery-staging-acceptance-plan/validation.json
```

## 未识别文件

当前为空。执行前以完整 dirty-path 差集复核；出现新文件时不使用通配符自动纳入。
