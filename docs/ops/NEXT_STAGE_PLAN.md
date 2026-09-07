# 下一阶段计划：恢复可靠的 staging RAG 评测

更新日期：2026-09-08，Asia/Shanghai。执行任务沿用
`.trellis/tasks/07-19-m5-observability-eval-load`；本阶段是 M5/M7 验收的前置交付。

## 当前判断

项目已具备上传、持久任务、检索、受控 Agent、审批、文档 ACL、身份绑定和审计治理的实现与测试。
当前最值得优先解决的是真实评测的执行可靠性，让模型效果可以被测量和诊断。

| 范围 | 已有证据 | 尚需完成 |
| --- | --- | --- |
| 本地工程基线 | 已发布 `a0d7439`，1017 项非集成测试通过；本次换行/工作区检查修复通过 1021 项；M0–M4/M8 已有通过的索引记录 | 新修复提交的远端 CI |
| 当前 staging | 已验收 `v0.1.33`；2026-09-07T18:08Z 只读检查为五个 Deployment 全部 1/1，依赖全部 up，约 1598 MiB 可用内存 | 持续的质量与运行证据；单节点不能证明 HA |
| M5/M7 模型质量 | 历史 v0.1.30 有一次 40 例基线；后续重复包含系统失败；v0.1.32 两例定向检查通过且有 token 统计 | 当前版本试跑、干净重复、稳定模型版本、费用依据、代表性语料和独立人审 |
| 评测启动 | 已准备 109 包 Linux 运行时 | 最近两次任务分别止于依赖同步和 Git checkout，均没有进入模型评测 |
| M6 交付与恢复 | staging 发布、治理 smoke、既有回滚/恢复记录 | 独立故障域恢复和独立复核仍开放 |
| 企业扩展 | 最小 RBAC/ACL、受限 SCIM、OIDC JWT 校验、retention/legal hold 与归档 | 真实 IdP 接入、完整 provisioning、独立审计存储等按实际需求单独验收 |

当前工作树在启动本阶段前干净；失败记录提交 `10aaebf` 已随托管实现 `a0d7439` 推送。
常规 GitHub Quality run `34142156900` 成功，而 trial `34143634860` 在 staging
runner 的 Git TLS/TCP 连接阶段失败。现有观测不能把该故障归因于 CPU/RAM 不足。
PR #3 已合并，但旧任务/worktree 的登记仍在；它们属于单独整理事项，本阶段保留。

事实入口：

- [证据索引](../../evidence/index.json)
- [v0.1.33 staging 验收](../../evidence/m6/20260904-v0.1.33-staging-governance.json)
- [依赖同步失败](../../evidence/m5/20260906-staging-rag-trial-33976542098-setup-failure.json)
- [运行时准备](../../evidence/m5/20260907-staging-rag-evaluator-runtime-preparation.json)
- [checkout 失败](../../evidence/m5/20260908-staging-rag-trial-34143634860-checkout-failure.json)
- [M5 质量门槛](../../evidence/gates/m5-real-provider-quality.json)
- [M7 质量门槛](../../evidence/gates/m7-real-provider-quality.json)
- [已有发布范围决议](../../evidence/gates/release-scope-decision-20260814.json)

## 优先级与执行顺序

| 优先级 | 交付 | 验收标准 |
| --- | --- | --- |
| P0，本阶段 | 可靠的评测执行路径 | 托管运行器完成预检；显式批准的一次 12 例试跑具有可核验的终态与对应报告或失败证据 |
| P1，后续质量验收 | 当前路由的完整质量与重复性 | 先审阅小样本；准备模型版本、费用依据、代表性语料及独立语义复核，再安排预先确定次数的 40 例执行 |
| P1，后续运维验收 | 当前 4C4G 上可实施的观测、容量与事件响应 | 固定工作负载/资源边界，保存测量与告警处理证据；生产容量和单节点诊断分开描述 |
| 外部条件到位后 | M6-R5 独立恢复 | 备用目标、事先批准的 RPO/RTO 和独立审核人到位后执行 |
| 按真实需求排期 | IdP、完整 SCIM、WORM/独立存储 | 先确定提供方、权限与合规要求，再建立专门的端到端验收 |

GPU/vLLM 容量遵循已有明确范围排除。本阶段不改变应用模型路由、staging 镜像、
数据集阈值、数据库、网络策略或服务器规格。

## 本阶段设计

1. 保持同一个手动工作流，新增 `execution_mode=validate-only|evaluate`，默认 `validate-only`。
2. `validate` 作业使用 GitHub 托管 `ubuntu-24.04`，不绑定 staging Environment，也不读取应用凭据。
   它从冻结锁文件安装运行时依赖，校验 v2 的 12/40 两组选例，并输出单独的 validation 报告。
3. `evaluate` 作业必须依赖成功的 `validate`，且仅在显式选择 `evaluate` 时运行。
   它在另一台托管 VM 中使用同一冻结环境，通过公开 HTTPS API 与对象存储访问 staging。
   不提供 Kubernetes 或 SSH 凭据；短期 smoke token 和主机白名单只进入评测步骤。
4. 保留共享 staging 并发锁、五分钟依赖同步边界、40 分钟评测作业上限、1800 秒模型评测窗口、
   串行案例、12/40 范围选择与当前 run/attempt 的精确报告路径。
5. Python/uv 设置复用仓库已固定的 Actions SHA 与版本。checkout 只取所选提交并不保留 Git 凭据；
   当前 provenance 只需要 HEAD 与工作树状态，不需要完整 Git 历史。
6. 现有服务器运行时和 wheelhouse 留作恢复材料。托管方案的可达性要由实际执行验证；
   若 API/对象存储网络仍失败，保留本次证据并诊断对应环节，不自动改变出口或重跑挑选成功结果。
7. 两个作业在 frozen sync 后、所有 evaluator 调用前检查干净工作区。历史 evidence JSON/log
   使用 `-text` 保留原始字节，避免旧 CRLF/mixed blob 被 clean filter 误判为修改；报告验收仍检查 dirty 标志。

GitHub 的官方说明确认每个托管作业使用新 VM，Environment secret 在配置的保护规则通过后才可访问。
当前 staging 配置只有 ref 限制，没有 required reviewer；环境名称本身不代表独立审批。

## 执行清单

- [x] 核查代码基线、最新失败、里程碑门槛及 live readiness。
- [x] 写出本计划，建立当前 Codex 目标，沿用 M5 任务。
- [x] 先增加失败的工作流边界测试，再实现托管预检与受保护评测。
- [x] 验证默认预检无应用凭据、12/40 选例及报告完整性；验证依赖/预检失败不能进入模型阶段。
- [x] 更新工作流规范、4C4G 手册和 PowerShell 操作步骤。
- [x] 完成非集成测试、Ruff、mypy、Actionlint、Trellis 与 diff 校验。
- [x] 提供具体提交及发布/单次试跑清单。
- [x] 取得这份具体清单对应的确认。
- [x] 发布 `a0d7439` 并验证对应 SHA 的 Quality CI `34158239990`。
- [x] 执行 hosted validate-only `34158544296`，保留成功步骤及两份 dirty 报告的拒收结论。
- [ ] 发布换行属性与干净工作区检查修复，核验新 SHA 的 Quality 与新 hosted 预检。
- [ ] 重新核查 staging、短期凭据和预算边界，执行一次批准的 12 例试跑。
- [ ] 保留终态、版本、选例、报告校验和服务状态；将结果交还后续质量审阅。

本阶段完成条件是可靠执行路径已发布并经过实际预检，且一次批准的试跑结果被如实保留。
出现质量失败时必须保留失败，不以重跑替代诊断；出现启动/网络失败时，执行路径的验收仍未完成。
单次 trial 或 validation 成功都不能将 M5/M7 的完整质量门槛改为通过。

## 本地实现与验证

2026-09-08，发布 `a0d7439` 前完成的工作流、回归和手册验证：

- 三项工作流行为分别经历红→绿：默认预检/显式 live 门槛、托管运行时、独立且精确的预检产物。
- 23 项工作流/评测定向测试通过；全量非集成测试为 **1017 passed，125 deselected**。
- Ruff format/lint、mypy（161 个源文件）、Actionlint 1.7.7、Trellis context 与 diff 校验通过。
- 实际离线 CLI 生成了 12/40 选例报告，校验了选例 ID、载荷校验和及数据集/语料/锁文件哈希。
  CLI 回归在移除 smoke token、禁止构造 staging 网络客户端的条件下也通过。
- 手册中 8 个 PowerShell 代码块语法通过；报告校验器通过 11 个正反用例，覆盖类别误用、SHA、
  脏工作区、范围、选例、数据集哈希、执行范围和载荷篡改，并保留合法失败质量报告供诊断。

这些是本地工程证据。离线报告记录的 evaluator HEAD 是 `10aaebf`，`working_tree_dirty=true`，
不会被当成已发布的托管结果。代码检查确认 provenance 仅查询 HEAD 与工作树状态，支持浅 checkout；
首次托管运行已完成依赖下载，但干净来源验收失败；公网可达性仍须通过真实 trial 验证。

## 已确认的提交与执行清单

已创建并推送 `a0d7439`：`fix: run staging RAG evaluation on hosted runners`，包含以下 10 个文件：

```text
.github/workflows/evaluate-staging-rag-quality.yml
tests/foundation/test_ci_contract.py
tests/evaluation/test_staging_rag_quality.py
.trellis/spec/backend/observability-eval-load.md
.trellis/tasks/07-19-m5-observability-eval-load/prd.md
.trellis/tasks/07-19-m5-observability-eval-load/design.md
.trellis/tasks/07-19-m5-observability-eval-load/implement.md
docs/ops/NEXT_STAGE_PLAN.md
docs/ops/staging-observability-and-capacity.md
docs/ops/single-node-4c4g-staging-runbook.md
```

发布前未发现来源不明的脏文件。当时远端 `main` 回读为
`c1e2ec7a6bb80da8fc68dc090d756fede8e5b716`；已获先前提交授权的 `10aaebf` 随本次发布推送。
2026-09-07T19:53:21Z 的发布前只读复核确认：默认 workflow token 权限为 `read`，Actions PR
审批关闭；staging Environment 仅允许 `main` branch 和 `v*.*.*` tag，没有 required reviewer，
管理员仍可 bypass。当时没有 queued/in-progress 工作流。Smoke secret 的最后更新时间仍为
`2026-09-07T16:29:24Z`；这只是元数据，不证明 token 有效期或成员状态。本次复核未修改远端。

已确认的执行顺序如下；任一步失败都先保留终态并诊断：

1. 精确暂存上述文件并创建新提交；不 amend。再单独执行发布，将 `10aaebf` 和新提交
   fast-forward 推送到 `origin/main`，推送前重新核对远端未变化。
2. 等待并核验新 SHA 的 Quality CI；运行一次 hosted `execution_mode=validate-only`，
   验证两份报告的干净 evaluator SHA、类别、选例、哈希及终态。
3. 核查 staging readiness、现有合成 smoke owner 和 Environment 的实际保护配置；通过既有
   issuer 刷新短期 token，仅以内存/stdin 更新 staging Environment secret。
4. 在 **GitHub 托管 VM** 上执行 **一次 12 例 trial**。每例串行，保留 1800 秒 evaluator
   和 40 分钟 live 作业上限。调用 ingestion/embedding/模型可能产生费用；这些是案例数与时间
   边界，不是自动金额上限。合成文档和 Agent 结果保留在现有租户。
5. 记录 run/attempt、evaluator 与 staging 版本、精确报告或失败证据、服务状态及限制。
   不自动 rerun、不扩大到 40 例、不把历史失败改为通过。

提交确认依据为 `.trellis/workflow.md` Phase 3.4；推送确认依据为工作区 `AGENTS.md`。
本次确认也覆盖短期应用令牌进入新托管执行位置及这一次可能计费的真实试跑。
用户在审阅这份具体清单后于 2026-09-08 回复“继续”，授权按上述顺序执行。
服务器已有运行时、wheelhouse、staging 镜像、模型路由及所有历史证据保持现状。

## 首次托管预检与必要修复

`a0d7439ba89b414fc74a55cf716dfcf80f2922b8` 已发布；Quality `34158239990` 的前后端
作业均成功。托管预检 `34158544296`（attempt 1）在 2026-09-07T20:13Z 完成，
依赖同步、12/40 数据集校验与上传均成功，`evaluate` 被跳过。

两份原始 sealed JSON 的选例、数据集/语料哈希和载荷完整性均符合契约，但均记录
`working_tree_dirty=true`，因此来源验收拒收。原报告和
[失败记录](../../evidence/m5/20260908-staging-rag-validation-34158544296-provenance-failure.json)
分别保留 GitHub 的 `success` 和验收的 `rejected`；没有真实模型调用。

使用独立临时 Git index、原仓库对象和 Linux 换行设置 materialize 当前 HEAD，复现 84 个
历史 evidence 文件被判脏；规范仓库 index 未变。原因是旧 CRLF/mixed blob 与 `text eol=lf`
的 clean-filter 规则不一致。三例真实历史文件的字节/回写 Git blob 测试先失败，改用 `-text`
后通过；新增工作流检查同样完成红→绿，要求两个作业在所有 evaluator 前拒绝脏工作区。

修复后的非集成回归为 **1021 passed，125 deselected**；Ruff lint、mypy（161 个源文件）、
Actionlint 1.7.7、Trellis 与 diff 校验通过。格式检查发现一处测试断言换行，已按 Ruff 格式修正。

接下来发布这项验收所需修复并核验新的完整 checkout 与 hosted 预检。只有新报告满足干净
来源契约后，才刷新短期 token 并使用尚未消耗的一次 12 例真实试跑授权。

## 调研依据

通过 smart-search-cli 的 Context7 文档检索定位，并实际获取以下 GitHub 官方页面：

- [Using GitHub-hosted runners](https://docs.github.com/en/actions/how-tos/manage-runners/github-hosted-runners/use-github-hosted-runners)：每个作业单独创建并回收 VM。
- [Deploying with GitHub Actions](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments)：Environment 的保护规则、secret 可用时机、并发组及托管网络边界。

检索命令为 `smart-search context7-docs /websites/github_en_actions`；正文使用
`smart-search fetch <上述URL> --format markdown` 获取。当前仓库与服务器事实来自只读 Git、
GitHub CLI 和 Tailnet SSH 检查，不来自搜索摘要。
