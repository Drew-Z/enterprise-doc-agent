# 复用现有 Email Routing 域

## 边界与数据流

playarchive.eu.cc 的既有 MX → 两条精确收件规则 → docagent-private-mail email handler → 同一 D1 → 既有 HTTPS 网页。其余地址继续匹配原 catch-all forward。

账号 2741446a7478f2d8a5ff31df7e077f17；收件 zone 18d121f8bd5d2692d448ebdbe7ea3071；D1 8521706f-87c6-43af-8866-cc0ce95dd5d9。上传前读取 active deployment，要求唯一版本 100% 且匹配恢复基线。

## 运行时变更

通过 deploy.prepare 验证固定包，深复制配置形成运行时 overlay。DEFAULT_DOMAINS=[playarchive.eu.cc]；DOMAINS 在旧两域前加入新域，保留兼容性。只用已验证的 worker.js 上传，metadata 使用 keep_assets=true、keep_bindings=[secret_text]，移除 assets JWT 并显式保留 assets.config.run_worker_first=true。不修改 bundle 或首次部署流程。

更新后明确 PATCH script-settings 关闭日志，再回读 active version 的 bindings、runtime、handlers、ASSETS 与 D1。observability:null 只能结合本操作已确认的 disable 请求解释。若身份漂移、未知配置、部分失败或超时，记录状态并停在该步，不盲重放。

## 邮箱与路由

扩展现有 public-site.mjs 的 --domain 和 --address-prefix，默认保持旧行为，URL 固定原网页。新的 mailbox JSON 使用独立受保护文件，逻辑键仍为 owner01/member01。先验证新邮箱创建与登录，再添加启用的 literal/to/完整地址规则，action worker/docagent-private-mail。创建前若出现任何相同地址规则则停止，核验后选择单独复用流程，避免重放创建，完整旧规则保持不变。

## 恢复与兼容

复用 D:/Agent/codex/backups/tasks/20260918T015708.345Z-saas-mail-routing-reuse。云端原 deployment/version、配置、D1 设置与计数、DNS 和完整路由保存私有快照并核验哈希。三份已跟踪文件用 e301adecf4eccf1220083f09167bf8537eb20460 恢复引用；父任务 WIP 已实体备份。

若上传后验证失败，不启用新路由；保留新邮箱及失败证据供恢复。需要回退时从固定程序和原配置重建 metadata，保留 assets/secrets/D1；任何规则删除或其他历史资源清理须另定具体范围。本次不执行删除。

## 验证边界

2026-09-18 实测修正：固定上游程序在管理员建址时也通过默认 /[^a-z0-9]/g 清洗 local part，第一次请求带连字符而实际创建 docagentowner01。保留失败报告，读取 D1 确认本任务新建 id=3，使用既有管理员 show_password 接口恢复该邮箱 JWT 到私有文件。选用全小写字母数字前缀 docagent，不改变 Worker 程序/正则、不删除已创建邮箱。runner 在发请求前拒绝不兼容前缀，限制前缀 22 字符，使较长 member01 的 local part 不超过上游 30 字符。

使用实际 Cloudflare 配置读取、HTTPS/API 和 Chromium；不 mock 业务实现，不发送邮件。只读基线体现新域/规则缺失；更新后以真实边界验证达到验收要求。runner 参数属于小幅运维改动，做语法/参数检查与真实验收，不增加照抄实现的单元测试。
