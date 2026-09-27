# Cloudflare 告警启用与验收

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

该 Worker 先覆盖公网 readiness。队列积压、备份新鲜度、监控自身失联及整个商业
验收的其余门槛仍需各自证据；不将其写成已经完成。
