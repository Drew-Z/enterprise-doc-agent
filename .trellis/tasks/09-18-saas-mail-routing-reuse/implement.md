# 执行计划

- [x] 恢复上下文、工作区审计、确认既有恢复组及原 WIP 基线。
- [x] 完成 PRD 收敛与主代理规划审阅；依据用户换域及此前择域授权实施，不宣称用户逐字审阅规划。
- [x] 刷新目标域和当前 Worker/D1 基线，集中保存私有快照与哈希。
- [x] 扩展现有 runner 参数，Node 语法与 7 项非法参数检查。
- [x] 固定包验证、审查只有两项变量变化，更新 Worker、明确关闭日志并严格回读。
- [x] 创建受保护的新 mailbox 文件；确认上游名称清洗后改用 docagent 前缀，恢复已创建邮箱凭据；新域 22 项、旧域 21 项检查通过。
- [x] 创建两条精确规则，逐条回读并比较原 DNS/catch-all。
- [x] 更新 README、spec、父任务汇总与结果；对照 1129 条 WIP 基线，仅允许父任务改变。
- [x] 执行质量检查，保留错误证据，清理唯一 pytest 临时目录。
- [x] 形成精确提交计划；父任务含原有未跟踪 WIP，排除在提交范围外。
- [ ] 获得本次提交计划的一次确认后提交；不自动 push/归档。

## 检查命令

使用仓库 .venv/Scripts/python.exe -X utf8 -B；node --check tests/cloudflare_mail/public-site.mjs；真实 runner 使用 --domain playarchive.eu.cc --address-prefix docagent。部署边界 pytest、Ruff、strict mypy 与后端非 integration 测试遵照仓库 quality spec；固定包未改不重建。

## 每次云端写入门槛

先持久化 started；验证操作前身份/冲突；请求后记录 succeeded 和回读证据。发生不确定结果时保留报告，不重试 POST/PUT。新规则只在新地址创建和登录通过后启用。真实发送/接收验收继续明确为未执行。
