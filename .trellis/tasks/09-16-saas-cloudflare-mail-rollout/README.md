# 私有邮箱公网基础接入

已上线 [DocAgent 私有试用邮箱](https://inbox.ciallobill.ccwu.cc/zh/)。2026-09-16 的真实 HTTPS/API/Chromium 验收通过，入口使用站点密码，邮箱使用各自凭据。当前只有两个合成测试邮箱；收信子域尚未开通，未发送真实邮件。

## 如何进入

1. 打开上述网页。
2. 从本机 `D:\Agent\codex\secrets\docagent-private-mail\credentials.json` 读取 `PASSWORDS` 数组中的站点密码。该字段本身是编码后的 JSON 数组；使用数组里的密码值。
3. 从同目录 `mailboxes.json` 读取 `owner01.jwt` 或 `member01.jwt`，填入网页的“邮箱地址凭据”。凭据不要放进 URL、聊天、截图或 Git。

两个地址为 `owner01@mailtest.ciallobill.ccwu.cc` 和 `member01@mailtest.ciallobill.ccwu.cc`。这两个名称只是邮箱测试账号，不代表产品租户的 owner/member 权限体系。管理员密码及 JWT 签名密钥不用于普通网页登录。本机两个凭据文件与目录均使用受保护 DACL，仅允许当前 Windows 用户和 SYSTEM。

## 实际完成

| 项目 | 当前结果 |
| --- | --- |
| 专用数据库 | `docagent-private-mail` / `8521706f-87c6-43af-8866-cc0ce95dd5d9` |
| Worker | `docagent-private-mail`，原固定包的 JS 与 27 个 assets |
| HTTPS 域名 | `inbox.ciallobill.ccwu.cc`，证书校验开启，HTTP 200 |
| 部署版本 | `a7b3cb12-0210-4050-bfb6-a4acb69d679c`，唯一 100% 版本 |
| 私有默认值 | 三组独立 secret bindings；workers.dev、预览、公开注册/建址、发信、自动回复/转发与 cron 关闭 |
| Worker 日志 | 正式 script-settings 接口明确接受关闭采集、持久化及导出；回读 `observability:null`，原样保留于报告 |
| 浏览器统计 | 关闭 `ciallobill.ccwu.cc` 的 Web Analytics；其他 5 个站点设置未变 |
| 公网验收 | 最终 21 项通过；外部请求 0，页面错误 0，浏览器已关闭 |
| 数据库回读 | 2 addresses / 0 raw_mails / 0 sendbox；固定初始设置保持一致 |
| 既有资源 | 3 个旧 D1、旧 Worker 和旧 custom domain 的已记录元数据一致；Routing 配置与规则未变 |

数据库创建使用 `primary_location_hint=apac`，关闭 read replication。最终查询实际报告 APAC / SIN / primary；这只是该次查询的观察，不能作为持续驻留新加坡或邮件/Worker 全链路固定处理地区的保证。真实客户资料、身份链接及正文保留策略仍未接入。

## 验证与证据

- [最终验收汇总](validation.json)、[云端回读](cloudflare-final-verification.json)、[DNS/HTTPS](dns-https-01.json)。
- [最终公网报告](evidence/public-02/report.json)：21 项通过。两个邮箱创建于 public-01，public-02 复用其本机凭据，没有重复建址。
- [第一次公网报告](evidence/public-01/report.json) 保留失败：业务/API/登录成功，但浏览器尝试请求 `static.cloudflareinsights.com` 两次，均被拦截。
- [浏览器诊断](browser-external-probe.json) 确认 Cloudflare 注入统计；[关闭记录](web-analytics-disable-01.json) 与最终 0 外部请求共同验证修复。创建单主机排除规则曾收到 409 / 10012 / `maxRulesError`，失败及只复现一次的诊断均保留。
- [发布结果](publication-01.json) 只设置日志和挂域名，不重放数据库初始化、assets、Worker 或秘密上传。此前四次失败记录保持原始状态，见 [研究记录](research.md)。
- 21 项部署边界测试通过，包含在最终后端 **1356 passed / 312 deselected / 75.45s** 中；Ruff、应用 mypy 219 文件、邮箱 mypy 5 文件通过。没有将旧 29 项本地验收算成新的公网检查。
- [源码来源](source-index.json)、[质量日志](quality-evidence-index.json)、[改动清单](changed-files.json)、[清理记录](cleanup-report.json)。

## 下一步

按 [收信子域步骤](next-steps.md) 在 Cloudflare 控制台添加 `mailtest.ciallobill.ccwu.cc`，再核验 DNS 并设置两个精确收件规则。网页能登录不代表已经能收信。

Email Sending、允许试发的收件人、长期 HTTPS Keycloak、实际身份托管地区、正文保留/删除、产品 OIDC HTTP/session 全旅程以及真实客户试用都仍未完成。身份方案继续采用 Keycloak；不需要另购 SMTP 服务，但私有 SMTP/API 适配器和 Cloudflare 发送能力尚未完成真实链路。

## 恢复与提交

集中恢复组为 `D:\Agent\codex\backups\tasks\20260916T054248.861Z-saas-cloudflare-mail-rollout`，入口见 [恢复引用](recovery-reference.json)。保留父任务、两个已有索引的基线字节，以及修改前的 Web Analytics 设置。旧固定包由已核验 Git `eca462385da540fef163e80d90e82e55383f05d4` 恢复。

新资源为长期测试基础设施，不作为临时文件清理。任何下线/删除都需要列出域名关联、Worker、D1 的精确 ID 和数据损失范围再获得相应授权；不自动销毁。启用旧统计设置也应按快照明确执行，不覆盖其他站点。

本轮源代码与交付尚未提交、推送或归档。具体范围见 [提交计划](commit-plan.md)；父任务和两个索引原本有 WIP，本轮增量留在工作树，未混入新增文件提交范围。
