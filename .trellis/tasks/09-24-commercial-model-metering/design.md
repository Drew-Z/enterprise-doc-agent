# 模型计量设计边界

用户已确认：一次成功的 Agent 业务任务结算一次任务额度；实际每次供应商调用独立记录；文档解析使用独立处理配额。额度计量不等于已确定人民币价格或真实付款。

复用 Tenant 的准入锁、有限周期和持久 Job。售前账本的 metric 约束和短 TTL 不适合长审批任务，新增按周期/metric 隔离的 product_quotas、product_usage_reservations、product_usage_events，保留售前账本含义。新模型调用记录不引用售前 operation FK。取消、fencing 丢失、未知上游结果和重启均保留观察，不默认成本 0。

Agent 以 run ID 预约一个 agent_task；文档以持久处理工作为边界，使用 document_bytes 原文件字节额度（独立于存储占用，失败不扣成功处理量）。这只决定额度单位，不设定价格。预约持有至持久业务终态，不按 900 秒自动释放：审批期间额度保持占用，取消/拒绝/到期/终态失败释放，成功只结算一次，跨周期仍归原周期。不能在预约已释放后继续模型调用。

锁协议：新预约先锁 Tenant，随后业务行、quota、reservation；同一 metric 的计数器修改统一先 quota 后 reservation。已有业务终态结算/释放只获取 quota/reservation，不在持有 run/job 的情况下反向申请 Tenant。额度配置只追加周期及其配额，不回头锁业务行。供应商 I/O 始终在短事务之外；Job fencing 与业务状态、账本终态必须在同一事务核验。

行为切片与公开测试边界：先通过 EntitlementAdministrationService.configure/show 验证独立额度配置与重放；再通过 ProductUsageService 验证最后一个名额的真实 PostgreSQL 并发、原周期结算、取消释放和跨租户隔离；然后接入 Agent/API/Worker、文档处理和 owner 用量页。只替代 HTTP/模型/对象存储等外部边界，不模拟数据库锁或内部账本服务。

界面先明确额度覆盖范围，不能展示售前次数为全产品账单。新增数据库结构使用新迁移，旧迁移不可改；费用事件保留时downgrade必须拒绝丢历史。

补充实现边界：

- 0029 增加三张产品额度表及 ingestion generation 的 processing_job_id；0030 增加
  provider_dispatches。文档 receipt 用 Job ID + max_attempts，自动重试共用，人工重开
  新建逻辑 receipt。同 generation 的活跃处理归属不能被另一个 Job 抢占。
- Tenant 准入/额度及 Agent 取消/审批使用 FOR NO KEY UPDATE，保留并发互斥，同时允许
  dispatch/event 的 FK key-share。真实 PostgreSQL 已重现旧排他锁死锁并验证修复。
- ProviderCallService 在每次实际 HTTP 前检查 ACL、Job 租约/fence、取消和原预约，并提交
  intent；HTTP 在事务之外。次数预算按企业 UTC 日、Agent run、文档 receipt、检索 receipt
  分别限制。MCP 显式传入 app_env；售前检索还检查原预约缺失、释放及过期。
- responded 只代表 HTTP 响应，语义错误仍可能已付费；dispatched/timeout/cancelled 等保持
  不确定。无价目表的金额和币种为 null。售前 Chat 沿用原调用表，运维探针不混入业务账本。
- configure-products 为已有周期补缺失配额，使用该周期 version，幂等且不修改既有上限/
  已用量。新 configure 默认 Agent/文档配额均为 0，需显式配置；新演示为 0 / 10 MiB。
- 严格 Web API 与后端需一起升级。审批跨周期继续使用原预约；当前周期的调用统计按
  started_at 汇总，不能与原周期业务用量相加作账单。
