# 本轮提交计划（未执行）

建议一个提交：`feat: add private Cloudflare mailbox deployment package`。

拟纳入本轮新增的文件：

- `infra/cloudflare_mail/` 下源码、固定来源锁、私有配置和说明。
- `tests/cloudflare_mail/` 下入口行为测试、构建保护测试与本地验收脚本。
- `.trellis/spec/infrastructure/backend/cloudflare-mail.md`。
- 本任务目录下的规划、来源、报告、ZIP、解包目录与必要验证证据；逐路径清单见 [changed-files.json](./changed-files.json)。被普通 `*.log` 规则忽略的本轮证据需逐个精确纳入，不能对整个工作区使用 force add。

以下三个既有文件的本轮增量留在工作树，不随这次新文件提交混入既有历史：

- `.trellis/spec/infrastructure/backend/index.md`
- `.trellis/spec/foundation-tests/backend/index.md`
- `.trellis/tasks/09-11-saas-first-use/task.json`

这三项在本任务基线前已处于未提交状态，父任务文档还属于既有未跟踪历史。后续统一整理 first-use 历史时再提交；不能直接整文件暂存，冒充只有本轮变化。

其余既有差异逐路径列在 [workspace-baseline.json](./workspace-baseline.json)，不纳入本次提交。原有 Windows 缓存变化、其他 worktree、产品代码和历史问卷/身份任务也不纳入。

执行前重新校验本轮清单和 HEAD/index，再仅暂存上述新增文件。不会 amend、push 或自动归档。仓库工作流 Phase 3.4 要求展示这份具体计划并取得一次确认后才能执行提交。
