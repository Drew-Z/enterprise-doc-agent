# Cloudflare 私有邮箱部署包与本地验收

固定版本的私有邮箱已实际构建，并通过本地 workerd、D1 和 Chromium 验收。这里交付的是初始只收信包；公网邮箱、真实投递和完整产品身份旅程仍待接入。

## 交付物

- [部署 ZIP](./delivery/docagent-private-mail-v1.12.0.zip)，1,772,272 字节，内含 38 个文件。
- [可直接审查的解包目录](./delivery/bundle/README.md)及[逐文件 SHA-256](./delivery/bundle/manifest.json)。
- [验证汇总](./validation.json)、[最终本地报告](./evidence/run-04/report.json)、[截图审查记录](./evidence/visual-review.json)。
- [源码与官方依据](./research.md)、[当前账号权限观察](./cloudflare-access-current.json)。
- [清理范围](./cleanup-plan.json)、[清理回执](./cleanup-report.json)、[工作区保护报告](./workspace-protection.json)。
- [本轮变更清单](./changed-files.json)和[提交计划](./commit-plan.md)。

ZIP SHA-256：`47e0999fd25ab1d8dfe76072e617c5cc90bdba62aa1b1d46b55d634e29536e62`。

构建入口为 `infra/cloudflare_mail/build.py`；使用固定上游 v1.12.0、210 项来源文件及两份冻结依赖锁，实际 pnpm 10.10.0、Wrangler 4.129.0。原始源码未改；构建副本只移除未启用的 Turnstile 外链，并记录修改前后的哈希。此处提供固定输入的重建步骤与实测产物，不宣称已证明跨平台逐字节一致。

## 本轮结果

| 验证 | 实际结果 |
| --- | --- |
| 私有 HTTP / email 入口行为 | 16 项通过，包含真实红绿记录 |
| 构建输入保护 | 10 项通过，已包含在下方 Python 总数中 |
| Worker / 前端构建、Wrangler types、私有入口 TypeScript | 通过 |
| 真实本地 workerd + D1 + Chromium | 29 项检查全部通过 |
| 浏览器 | 站点与邮箱凭据登录成功；0 次外部请求、0 个 pageerror；3 张最终截图已查看 |
| 本地 D1 | 2 个地址、2 封合成邮件、0 条发送记录；列表及按 ID 读取均隔离 |
| 后端非 integration 测试 | 1335 passed，312 deselected，63.61 秒 |
| Ruff | 510 个 Python 文件格式通过，lint 通过 |
| mypy | 应用 219 个文件、邮箱与构建测试 3 个文件通过 |

上游浏览器会保存站点/邮箱凭据；截图只展示空凭据输入框或合成收件箱，不含 JWT、密码、身份链接、地址栏、trace 或 video。最终验收已关闭浏览器、Worker、D1 proxy 与监听端口。

## 保留的失败证据

1. `build-01` 缺少根级 `tsc` shim，随后直接执行冻结依赖中的 TypeScript 通过；构建器已采用此入口。
2. `run-01` 的运行时不支持兼容日期 2026-09-16；原报告保持失败，部署配置改为实测支持的 2026-09-10。`build-02` 成功不等于这轮运行成功。
3. `run-02` 和 `run-03` 在登录及收件箱通过后发现两次 Turnstile 请求；第三轮仅增加 origin 诊断，确认来自 challenges.cloudflare.com。`build-04` 去掉固定外链后，`run-04` 通过全部检查。
4. 合并检查 Python namespace package 与测试时，mypy 初次因模块路径重复而拒绝；使用 `--explicit-package-bases` 后通过，没有关闭类型检查。

## 账号与后续接入

2026-09-16 03:00 UTC 的实际读取确认 Token active，策略包含 D1、Workers、邮件路由/地址和 Email Sending Write；不需要重复补这些权限。实际 Account/Zone 资源范围为 wildcard，任务仍限定既定账号及 ciallobill.ccwu.cc。Email Sending 子域接口仍为 401/code 2036，功能资格原因尚未核实。

账号内两项已验证 destination 只作盘点，没有获准真实试发的收件人。既有 D1、KV、Worker 和其他域名没有用于本任务。

下一阶段按[包内接入顺序](./delivery/bundle/README.md)创建全新专用 D1、注入独立 secrets，再配置 inbox、mailtest、notify 子域。先完成受控收信，再接 Keycloak 的私有 SMTP/API 适配器与明确名单的 Cloudflare 原生发送 binding。该架构不需要另购 SMTP 提供商；发送到任意外部用户仍受 Cloudflare 套餐与资格限制。

邮件保留期、D1/Keycloak/模型的正式处理地区、长期身份托管、完整产品 HTTP 登录及真实客户试用仍未完成。此前 AWS 新加坡/Cognito 方案保留为未部署历史选择；当前身份方案是 Keycloak。真实试用团队仍为零。

## 保护与任务状态

只修改本轮邮箱包、测试、任务记录、两处 spec 索引及父任务汇总。与 1128 路径基线相比，原有 `%SystemDrive%/ProgramData/Microsoft/Windows/Caches` 出现一个文件内容变化及三项版本文件更替；写入来源未确定，目录保持原样，未删除或回填旧缓存。此项与业务源码/配置保护分开报告。

当前交付不包含 Git 提交、推送或归档。任务保留 `in_progress`，本地交付/验收/清理结果分别记录；提交计划以新文件为界，既有未提交历史不整包带入。
