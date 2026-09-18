# 复用已开通域名接入私有邮箱收信

## 目标

按用户要求改用已经开通 Email Routing 的域名，让现有私有邮箱具有可核验的收件配置。

## 背景与授权

用户要求「换成这里面已经有的域名」，并说明存在历史邮件设置。依此前择域委托，主代理选择 playarchive.eu.cc；用户未逐字审阅本规划。DMARC Management 不是收信开通页，Locked 是必需邮件 DNS 的托管状态。2026-09-18 只读盘点发现该域已启用 Routing、只有邮件 DNS、已有兜底转发，两个拟用地址无精确规则冲突；实施前须刷新。

## 要求

- R1：收件地址为 docagentowner01@playarchive.eu.cc 和 docagentmember01@playarchive.eu.cc，网页仍为 https://inbox.ciallobill.ccwu.cc/zh/。
- R2：复用现有 Worker、D1、程序、27 个 assets 和三组秘密；旧邮箱凭据继续有效。
- R3：只添加上述两个地址到现有 Worker 的精确规则；原 DNS、兜底转发及其他域配置保持不变，不删除历史设置。
- R4：公开注册/建址、发送、自动转发/回复、Webhook、备用入口和持久日志保持关闭；浏览器外部请求为零。
- R5：凭据及原 forwarding destination 只保存在受保护的本地文件；所有恢复点集中在既有恢复组。保留原有工作区 WIP。

## 验收

- A1 / R1,R2：真实 HTTPS/API/Chromium 验证两个新地址，旧地址 API 鉴权仍成功；D1 使用同一 UUID。
- A2 / R2,R4：当前唯一 100% 流量版本仅变更 DEFAULT_DOMAINS/DOMAINS；runtime、其余 bindings、assets 安全配置及三组 secrets 保留，私有边界检查通过。
- A3 / R3：两条启用的 literal-to 规则指向 docagent-private-mail，DNS 和完整旧兜底规则与私有快照相同。
- A4 / R5：报告不包含秘密；恢复快照有哈希；非本任务 WIP 字节不变。
- A5：如实标为配置就绪；本阶段不发送或注入邮件，不能宣称公网实际投递已通过。

## 范围边界

不重建固定包、不初始化数据库、不重新开通旧 mailtest 子域、不修改其他六域；不处理 Sending、长期 Keycloak、真实试用或商业验收。Git 提交需要独立的具体计划确认，不自动 push 或归档。
