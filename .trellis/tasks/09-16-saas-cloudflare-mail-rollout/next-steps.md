# 接通收信子域

网页 [inbox.ciallobill.ccwu.cc](https://inbox.ciallobill.ccwu.cc/zh/) 已通过验收；当前收信 Routing 仍为 disabled/unconfigured，DNS 只有网页 Worker 记录。现阶段不要把测试地址当成已可投递邮箱。

## 控制台动作

当前公开文档给出的子域开通入口是控制台。本任务没有可操作的 Cloudflare 控制台登录会话；已有 API Token 不需要重新粘贴或修改。公开 API 的 DNS `subdomain` 查询参数已弃用，不能把它或根域 enable API 猜作子域开通接口。

1. 打开 [Cloudflare Email Routing](https://dash.cloudflare.com/?to=/:account/email-service/routing)，使用账号 `2741446a7478f2d8a5ff31df7e077f17`。
2. 选择 `ciallobill.ccwu.cc`，进入 **Settings → Subdomains**。
3. 按输入框提示添加 `mailtest`，确认结果显示完整的 `mailtest.ciallobill.ccwu.cc`。
4. 由 Cloudflare 为该子域生成所需 DNS，等待状态就绪。不要复制根域 MX/SPF/DKIM，不猜 MX 优先级，也不要进入付费或 Email Sending 开通流程。
5. 完成后只需反馈子域状态；可提供不含密钥的设置截图。如果界面先要求额外的 apex onboarding，先保留其具体提示，由后续检查确定步骤，不套用旧根域记录。

官方依据：[Email Service — Subdomains](https://developers.cloudflare.com/email-service/configuration/subdomains/)，原始正文已保存在本任务 source-index 中。

## 开通后的操作范围

先回读 zone DNS、Routing 域和规则，确认 Cloudflare 实际生成的记录及传播状态，再为下列地址配置精确匹配、目标为 Worker `docagent-private-mail`：

| 收件地址 | 动作 |
| --- | --- |
| `owner01@mailtest.ciallobill.ccwu.cc` | Send to Worker `docagent-private-mail` |
| `member01@mailtest.ciallobill.ccwu.cc` | Send to Worker `docagent-private-mail` |

保持 catch-all 关闭。数据库拒绝未知地址的规则已经启用，但它不替代域名的精确 Routing 规则。先检查同名规则，不能盲目重复创建。

本轮没有实际发送或接收邮件。真实投递测试还需明确可用的发件渠道和获准的测试收件地址，并只使用无真实身份链接、无客户内容的合成正文。账号 verified destinations 不自动构成发送授权。

## 之后的身份链路

统一身份服务仍选 Keycloak。既有本地实验已验证 Keycloak 26.7.0 的验证邮件、重置与 OIDC 客户端，但发信边界为本地 capture。

长期托管位置、HTTPS Keycloak、私有 SMTP/API adapter、`notify.ciallobill.ccwu.cc` 的独立 Sending 开通和产品完整 HTTP/session 验收还需继续落实。Cloudflare Email Sending 子域接口最近检查仍是 401；不能把它简化为 Token 缺权限，也没有启用付费计划。当前私有 Worker 没有 `SEND_MAIL` 绑定，不能拿它测试发信。

地区与数据保留必须分别记录：D1 APAC hint 和一次 SIN 查询不是全链路地区保证；关闭 Worker 日志和浏览器统计也不会删除 D1 邮件正文。在确定正文保留/删除机制及实际处理地区前，保持仅合成测试数据。
