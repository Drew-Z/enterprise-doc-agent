# 持续主任务：项目现状与首轮选择

记录日期：2026-09-09；规范仓库 `D:/workspace4Cursor/enterprise-doc-agent`，分支 `main`，基线提交 `ffca3d0861ae22c857fef54260b9fc4c083e980e`。

## 已确认现状

- 现有父任务覆盖 M0–M8 集成和发布验收，M0–M4/M8 已完成，M5/M6/M7 持续开放。复用此任务及原里程碑；不创建重复主任务或新工作区。
- 系统采用 Web/API/Worker/consumer 的模块化单仓库；PostgreSQL 保存业务状态和 durable attempt，Redis/Celery 负责投递，Agent checkpoint 与业务状态各自承担不同职责。
- 最近已验收 staging 是 v0.1.33。v0.1.34 两次部署均失败，第二次运行 `34248230396` 在 upload→Agent smoke 失败；稍后 Ready 不是发布验收。
- 第二次 QA 的 attempt 为 `permanent_failed`，诊断为空。当前安装源码一致、新解释器检查通过，只证明当前代码；历史执行进程和底层根因仍未建立。
- `scripts/staging_smoke.py` 原本只在成功时写 JSON，失败阶段及终态无法自动进入同一报告；这是已经从代码和运行证据确认的缺口。
- M5/M7 仍缺真实 provider 的重复性、代表语料、独立复核、revision 和计费证据；M6 仍缺独立故障域恢复及独立复核。当前本地修改不能关闭这些门槛。
- 开始本轮时工作树有 86 个已知待提交归档文件，暂存区为空。所有既有 worktree 保留，顺序任务复用规范目录。

依据：[父设计](../design.md)、[失败诊断交接](../../07-19-m5-observability-eval-load/research/20260909-v0.1.34-smoke-diagnosis.md)、[第二次失败记录](../../../../evidence/m6/20260909-v0.1.34-staging-redeployment-failure.json)。

## 执行决定

1. M6 子任务 [staging smoke failure evidence](../../09-09-staging-smoke-failure-evidence/prd.md)：先修复失败报告，完全使用离线假客户端核验，保持非零退出、现有请求数量和发布门禁。
2. 第一个子任务本地验收后，检查 consumer 装配、durable attempt 和结构化日志边界，再制定 M5 的执行关联子任务。不能把观测缺口直接写成历史故障根因。
3. 每次子任务本地完成后更新父任务队列、验收和下一步；已验收但待提交的子任务标为 `review`，记录 `local_validation=passed`。提交、归档和外部发布状态分别记录。

当前用户请求授权持续规划、实现和本地验证。既有工作流要求提交前对具体清单单次确认；此边界保留，但不阻止下一项独立本地工作。现有发布批准已使用完毕，不自动发起第三次部署或收费模型执行。
