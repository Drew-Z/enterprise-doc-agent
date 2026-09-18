# 已开通域名的私有邮箱收件接入

2026-09-18，配置与访问验收完成，等待本次 Git 提交计划确认。真实公网投递未测试。

## 使用入口

- 网页：https://inbox.ciallobill.ccwu.cc/zh/
- owner：docagentowner01@playarchive.eu.cc
- member：docagentmember01@playarchive.eu.cc
- 站点密码沿用原本机 credentials.json；邮箱 JWT 位于 D:/Agent/codex/secrets/docagent-private-mail/mailboxes-playarchive.eu.cc.json。两个文件均受保护，仅当前用户与 SYSTEM 可访问；秘密没有写入报告。

账号已有 Email Routing；之前选的 ciallobill.ccwu.cc 未完成 Routing onboarding。DMARC Management 是报告功能，不是开通收信的入口；Locked 表示 Cloudflare 管理必需邮件 DNS。用户要求改用已有域名，所以本次复用 playarchive.eu.cc，无需继续开通旧 mailtest 子域。

## 正式变更

Worker 仍为 docagent-private-mail，D1 仍为 8521706f-87c6-43af-8866-cc0ce95dd5d9。仅改变 DEFAULT_DOMAINS 与 DOMAINS；原两域保留，固定程序、27 个 assets、三组 secrets、其余变量、runtime 和网页地址不变。

新部署 425bc489-6905-4833-a61c-29d0e187a58e，唯一 100% 版本 a313c1c2-9e6c-4f0e-950d-756e0df4e5be。script etag 与原版本一致。明确请求关闭日志后，observability 仍原样回读 null；结合本次 disable acknowledgement 判定，未把 null 单独作为证据。

两条启用的 literal/to 规则均指向现有 Worker：

| 地址 | 规则 ID |
| --- | --- |
| docagentowner01@playarchive.eu.cc | 69511fcbcd6848f9aedbb42a996caaa9 |
| docagentmember01@playarchive.eu.cc | 2c73f9a615eb41d7905b4c1792f71661 |

原 5 条 DNS、兜底转发规则及其他六域路由完全相同；未删除旧设置。D1 最终 4 个合成邮箱、0 封收件、0 封发件，原记录及私有设置、禁用读副本保持不变。该结果不承诺固定地区驻留。

## 验证

| 检查 | 结果 / 证据 |
| --- | --- |
| 新域真实 HTTPS/API/Chromium | 22 项通过；public-new-domain-02/report.json |
| 旧域兼容与默认参数 | 21 项通过；public-legacy/report.json |
| 浏览器外部请求 / 页面错误 | 两次通过运行均为 0；浏览器均已关闭 |
| 线上 assets 与固定包比对 | 27/27 SHA-256 相同；assets-verified.json |
| 云端最终只读核验 | cloud-final.json，原 DNS/规则与其他六域一致 |
| 参数拒绝与语法 | 7 项通过、Node --check 通过 |
| 后端非 integration 测试 | 1356 passed、312 deselected、23 subtests passed，60.92 秒 |
| Ruff / 应用 mypy | 512 文件格式通过、lint 通过、219 源文件类型检查通过 |
| 邮箱及任务 helper 类型检查 | 4 个源文件通过 |
| 原 WIP | 1129 条基线中，仅父任务文件按计划更新；其余 1128 条字节未变 |

新旧站点检查仅验证私有访问、禁用功能、空邮箱和凭据；没有发出或注入邮件，不能据此宣称真实投递已通过。发送功能、Keycloak 长期部署、完整产品会话与商业验收仍是后续工作。

## 调整与保留的失败证据

首次管理员创建请求中的 docagent-owner01 被固定上游 /[^a-z0-9]/g 清洗成 docagentowner01。精确地址断言正确失败，未继续启用规则。读取 D1 确认 id=3 后，通过既有管理员 show_password 接口恢复 JWT；改用字母数字前缀，复用该记录并创建 member。没有删除、复制或重复创建 owner 邮箱。首次失败报告 public-new-domain/report.json、地址盘点与恢复报告保留。

只读 DNS 预检最初误将列表解码为 object，修正后重新捕获完整基线，错误尝试没有云端写入。应用 mypy 初次额外带了适用于邮箱模块的 --explicit-package-bases，造成模块发现错误；改用仓库正式应用命令后 219 文件通过。任务 helper 的两处 Any 返回检查也已修正，初始失败日志保留。

## 恢复与清理

恢复组：D:/Agent/codex/backups/tasks/20260918T015708.345Z-saas-mail-routing-reuse 。manifest.json 指向父任务原 WIP 备份、Git e301ade 的三份文件恢复引用、1129 条工作区基线，以及 files/cloudflare/cloud-before.json。云端快照 SHA-256 为 9ae0796ffbc029b9709caf3b8597cbff6b2b8f9e924f96d900c62ee4461aac67，含完整原转发目标，仅在受保护本机目录保存。

已删除本轮唯一 pytest 临时目录 C:/Users/zhang/AppData/Local/Temp/docagent-routing-pytest-49a29f6b2c464d37a2f05966fae1275a，验证已不存在。用户两张截图、历史 worktree、既有 %SystemDrive% 运行目录和所有原 WIP 保留。

## 提交边界

计划只提交当前任务交付物与三份原有文件（public-site.mjs、infra README、rollout spec）。父任务 task.json 含任务前未跟踪 WIP，虽更新汇总但不纳入本次提交。当前暂存区为空、HEAD 为 e301adecf4eccf1220083f09167bf8537eb20460；未提交、未 push、未归档。本次精确文件范围见 commit-plan.json 和 commit-plan.md。
