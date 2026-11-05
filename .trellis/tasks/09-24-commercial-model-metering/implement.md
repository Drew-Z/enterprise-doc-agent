# 实施顺序

1. CM-1已确认：成功 Agent 任务一次结算，文档解析另设处理配额；供应商调用和未知成本单独记录。
2. 完整读取AgentRunService、graph/gateway、Worker terminal/failure/cancel/approval路径和embedding ingestion，画锁/事务状态流；不只在API加一次余额查询。
3. 通过真实DB为单次Agent操作写预约/取消/终态/重复请求测试，逐行为红→绿。
4. 供应商调用按实际派发持久记录，预算、重试和未知结果与业务成功结算分离。
5. embedding处理配额、失败/重试以及统一成本视图；有限数据集验证无越权/串账/二次收费。
6. 同步API、Worker、使用页、运营文档和发布commercial_metering门槛。维护历史迁移与恢复点，不用隐藏Agent入口掩盖未完成计量。

## 2026-09-24 实现状态

已完成候选源码与本地验证：

- 0029/0030 新迁移、产品预约/终态事件、Agent/文档成功计量及取消/失败释放。
- Agent/API/Worker/MCP 及全部业务 embedding 的逐次派发记录和有限预算，跨周期审批和
  丢失预约/租约后的重试阻断；真实 PostgreSQL 并发 FK/额度死锁回归。
- configure/configure-products 正式与本地 CLI、owner API 和中英用量页。
- 运营手册补齐现有企业及演示配额、旧任务排空、版本一起升级和有历史禁止降级流程。
- 最终相关 PostgreSQL 回归 134 passed / 1 deselected（real_stdio 未覆盖）；售前背景回归
  26 passed，新增售前检索预约丢失 3 passed；CLI 9 passed；前端 37 passed；Chromium
  4 passed，桌面/手机截图已检查。测试 HTTP 使用替身，没有发起真实供应商费用。
- 早期更宽回归为 Python 非 integration 1708 passed / 418 deselected，另有 23 subtests；
  后续锁与 guard 修改使用上述相关测试重新验证，不把早期记录冒充全部最终源码重测。

仍未完成：真实供应商渠道对账/价目表、当前部署迁移与补配额、正式客户业务/容量/运维验收。
不改旧 v7 失败结果、gold 或 commercial-readiness 门槛；task 保持 in_progress。

## CM-5 对账准备切片（进行中）

1. 共享 HTTP 元数据提取与持久化：先请求标识回归红，再0031及Agent/embedding/售前成功和失败路径绿；无效/缺失标识保持 null。
2. 只读 UsageReconciliationService：真实 PostgreSQL 单企业、半开窗、事件与调用分离、历史缺口、超限拒绝、快照并发与只读断言。
3. 私有 operations usage export：显式目标核对、稳定 JSON、安全错误、有限参数及真实适配器验证。
4. 更新可执行规范/运营手册，运行针对性集成、非integration、Ruff/Mypy，精确提交推送 Draft PR #8并核查新SHA CI。

恢复点复用集中组；历史脏文件保留，不迁移 public，不发起付费模型调用或部署。

### CM-5 对账准备交付结果

- 0031 追加可空供应商标识和售前企业时间索引，保留旧 null；有新标识时拒绝降级丢失。
- 私有 usage export 已实现单企业、明确时区、最多31天、每来源最多5000条与一致只读快照；超限整体拒绝。
- 业务事件和供应商记录分开，保留原周期；旧同步汇总、未知费用/Token和未派发状态明确显示。
- 43项真实 PostgreSQL 回归通过，后补租户/时间/来源上限1项通过。完整非integration 1746 passed / 436 deselected / 23 subtests；最后收紧数字时间输入后，窗口/CLI/文档22项通过。
- Ruff/format 584文件，Mypy 249源码，任务上下文验证通过。各次本地失败和验证范围见 validation.json。
- 原 public 结构保持不变，本轮数据库测试各自删除自建schema；未调用真实模型、未发送通知、未迁移远端或部署。
- CM-5真实供应商账单/价目表及正式业务验收继续阻塞，任务保持 in_progress；不归档、不把本地结果提升为商用通过。
