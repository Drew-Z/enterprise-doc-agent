# Cloudflare 告警启用与验收

## 2026-10-09 当前运行配置

用户已确认 Outlook 收到唯一测试 `CLAW-20261008-01`。两个正式 Worker 均已切换到 ClawEmail，使用 `MAIL_PROVIDER=clawemail`、`MAIL_FROM=biau4z@claw.163.com` 和 `CLAWEMAIL_API_KEY` secret，收件人仍为原来固定的运维地址。分钟 cron 在 Cloudflare 执行，不依赖本机在线；不增加任意收件人能力。

| Worker | 当前版本 | 状态键 |
|---|---|---|
| `docagent-ops-monitor` | `10ef1efb-71d9-4cec-9bcb-1d17c0740f45` | `operations-live-v1` |
| `docagent-ops-watchdog` | `13415854-05ca-4e4f-86ec-fe0ce7c70f04` | `watchdog-live-v1` |

运行源码绑定 `7baac1b998566fdf215eb640abcde9376792f2fe`，兼容 Coremail 实际返回的 `text/x-json;charset=UTF-8`；认证端仍要求 `application/json`。严格 JSON、64KiB 上限、共用 10 秒截止、D1 先 claim、一次 deliver 和 unknown 不重试保持。103 项监控检查及对应 Quality/Container CI 通过。

2026-10-08 21:04 UTC 切换后，五次观测覆盖 21:04、21:05、21:06 三个自然分钟，两监控均 ready，51 条历史通知完整保留，没有新通知。独立云端探测在 589 毫秒完成认证及同一已发送邮件回执核对，共两次 API 请求、零发信。单封实际收件、云端只读路径、自然调度分别留证，详见[脱敏验收记录](clawemail-activation-validation.json)。

回滚恢复组保存切换前源码、设置、部署引用和调度；原 `MAIL` binding 保留供显式恢复，不能自动回退或重放 unknown。旧发件域仍未证明解除 Spamhaus DBL，恢复旧通道不代表恢复 Outlook 可达性。遇到问题先核对 D1 状态、Worker 版本及脱敏错误，不通过重复发送判断是否成功；凭据只能通过既定 secret 流程更换，不写入仓库或日志。

本轮五个临时 Worker 和三个独立 D1 均已删除并核对缺席，原探测超时、格式拒绝及定时窗口无结果保留。没有注入新的事故或发送新的故障/恢复邮件；实际值班处理、独立审核与客户 UAT 继续开放。此前备份延期保持，邮件接通不能替代备份验收。

## 历史接线与授权

以下内容保留各自时点，当前配置以上节为准。

2026-09-29 更新：主 Worker 已切换至 `6380c625-45d0-4eb8-9a14-2c62987675b3`，状态键 `operations-live-v1`，增加队列/备份心跳与 watchdog 新鲜度；独立调度 Worker `docagent-ops-watchdog` 为 `9e2ec181-06c3-4a20-8f5d-614c35e0d579`。两者先观察再启用 notify，分钟 cron、D1 和原限定收件人均已读回。下文 9 月 28 日版本和实收为历史阶段，仍保留。新增范围与实际限制见[最终验收入口](final-project-acceptance.md)。

## 现有邮箱可以复用

现有 CF Worker 负责收信，没有 `send_email` 绑定。官方当前 Email Service
文档说明：向账户内已验证目的地址发信，在所有计划免费；向任意未验证收件人
发送则需要 Workers Paid。此项目只有指定的运维收件人，无需先购买 SMTP 或 Resend。

- [官方定价与免费范围](https://developers.cloudflare.com/email-service/platform/pricing/)
- [目的地址验证](https://developers.cloudflare.com/email-service/configuration/email-routing-addresses/)
- [Workers 发信 API](https://developers.cloudflare.com/email-service/api/send-emails/workers-api/)
- [绑定的收件人与发件人限制](https://developers.cloudflare.com/email-service/configuration/send-bindings/)

初始只读核查确认既有收信域名处于 active、Email Routing ready，新资源名未占用；
现已创建专用 Worker/D1，用户点击验证邮件后，API 已确认指定地址验证成功。账户付费计划读取返回 403，
不据此推断订阅状态，也不启用付费的任意收件人发送能力。

## 本次具体接线

| 项目 | 配置 |
|---|---|
| Worker | `docagent-ops-monitor`，独立于原收信邮箱 |
| 数据库 | 新建 `docagent-ops-monitor-state`，只保存状态及通知事件 |
| 目标 | `https://agent.playlab.eu.cc/health/ready` |
| 周期 | 每分钟一次，10 秒截止，无重试，不跟随重定向 |
| 判断 | 核对 JSON、依赖状态、时间及大小，三次失败告警、两次成功恢复 |
| 邮件 | 固定指定发件地址和收件地址，双向 binding allowlist |
| 初始状态 | `observe`，记录状态但不发信 |
| 原服务 | 原邮箱收信、DNS 路由及服务器工作负载保持现有配置 |

私有执行计划保存精确账户、收件人、发件人和配置哈希；仓库不保存密钥。
首先验证源码、真实本地 workerd/D1 和发布 dry-run，再配置只观察的定时任务。

## 发信授权与实收

前一维护窗口邮件预算为零。本次用户已单独批准以下三封及实收确认后的日常告警：

1. 向指定收件人发 **一封 CF 地址验证邮件**，由用户点击其中的验证链接。
2. 地址验证后，通过独立测试 Worker 发 **一封故障测试、一封恢复测试**。
   测试明确标为 TEST，使用独立状态，不停止线上服务。
3. 用户核对两封邮件的事件 ID 和收信时间；测试发送结果、投递未知和实际实收分别记录。
4. 完成测试并获准日常通知后，启用正式监控。重复状态不重复发信。

如果地址已由用户自行验证，跳过验证邮件；若权限或发送域名检查失败，先停止
并修复配置。发信超时不自动再发，不通过重置事件规避三封测试上限。

用户也可以在 Cloudflare 控制台打开已有收信域名的 **Email → Email Routing →
Destination addresses**，添加运维地址并点击验证邮件。完成后只需告知已验证，
不用复制邮件链接、Token 或密码到聊天中。具体菜单名称可能随界面更新略有变化。

### 2026-09-28 实际结果

正式 Worker 已以 `observe` 每分钟运行，三个以上不同分钟的只读快照为 `ready`。
独立测试 Worker 使用固定五分钟窗口，2026-09-27 17:43 UTC 产生一次故障事件，
17:45 UTC 产生一次恢复事件；两次 binding 均返回 message ID，D1 为 `accepted`。
这是北京时间 9 月 28 日 01:43 和 01:45 的 TEST 邮件。没有停止或注入真实服务故障。

验证邮件请求一次，测试发信尝试两次，未重发或重置事件。测试 Worker 及其 cron
已经删除并核对名称消失，正式观察 Worker、原邮箱和 D1 事件保留。
用户已明确确认两封均已收到。正式监控随后以新状态键 `readiness-live-v1` 切换为
`notify`，固定发件/收件 allowlist、每分钟 cron 和部署版本均已读回；第一条真实
定时观察为 `ready`，没有触发日常邮件。原 observe 状态与 TEST 事件保持原样。
当前版本为 `bec0182c-62de-4863-b5b3-23e72a7ffb29`。

新 cron 发布前为平台传播预留了超过 15 分钟的窗口；预定时间调整发生在首次部署前。
没有在测试失败后重排时间或清空记录。源码、本地真实运行时测试、部署版本、
三分钟以上观察、两条发信事件与清理回执均存原集中恢复组。

## 放行和剩余范围

验收需同时记录实际部署版本、三次以上真实定时观察、故障及恢复各一个事件、
binding 返回的 message ID、用户实收回执、值班处理人及处理动作。仅 dry-run、
模拟收件、API 成功或用户未核对的发信记录，均不能关闭 CO-4。

主监控已接入队列、备份与调度新鲜度；新鲜/过期、失败/恢复、去重通过真实本地
workerd/D1 测试，线上真实心跳与 cron 已观察。共享 Cloudflare 的平台故障、Windows
离线时的持续备份、值班及整机恢复仍不在这些结果内，不将其写成完整商业通过。
