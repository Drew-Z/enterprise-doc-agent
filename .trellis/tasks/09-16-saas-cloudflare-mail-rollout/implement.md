# 实施顺序

- [x] 核验规范仓库/远端/worktrees、Git 提交和暂存区，运行工作区目录审计。
- [x] 保存 1128 条既有脏路径基线；创建集中恢复组并验证三份原件/备份哈希。
- [x] 只读核验真实账号/zone/DNS/D1/Worker/域名；读取当前官方 D1、Assets、Secrets、Routing API 文档。
- [x] 完成 PRD/设计收敛；本轮用户“继续”按既有授权推进独立测试资源接入，未声称用户逐字审阅新文档。
- [x] 用公共 prepare 接口做首个固定包篡改红测，实现只读验证。
- [x] 用注入 HTTP 边界验证冲突拒绝、错误停止、分页和无敏感输出；实现首次部署及脱敏状态记录。
- [x] 验证 Windows 凭据脚本语法与实际 DACL。
- [x] 运行部署预检并审查具体资源/配置，然后执行可逆新增资源部署。
- [x] 回读 D1/Worker 配置，核验真实 HTTPS/API 和浏览器；保留未完成邮件 DNS 的单独结论。
- [x] 运行相关 Python 测试、Ruff、严格 mypy 和共享后端检查；更新 spec 和父任务。
- [x] 核验旧包与其他 WIP，盘点新文件，清理本轮临时材料，准备本轮精确 Git 提交计划。

## 行为切片和验证边界

1. 固定包被同长度修改或附加文件 → prepare 拒绝，不产生网络请求。真实文件系统；合成小 fixture 标明非上游。
2. 目标 D1/Worker/域名已存在、账号不符、列表读取失败/不完整 → 部署停止，不产生写请求。httpx.MockTransport 只替代远程 API。
3. 首次部署有序成功 → D1 ID 仅来自创建响应；资产会话 JWT 仅用于资产上传；先封闭备用入口再写秘密，最后挂网页域。状态文件不含任何秘密。
4. 写入超时、响应形态异常、secret 上传失败 → 不继续挂网页域、不重试、不自动删除；状态保留已成功步骤和当前不确定步骤。
5. 实际公网站点 → 用真实 API/Chromium 验证，不 mock 认证或数据库。合成邮箱尚未收信不等同真实接收通过。
6. 配置回读从正式 script-settings 与当前部署版本取值；旧 API 假设红测后修正。日志 null 必须有当前操作明确关闭确认；禁用失败/仍启用则不发布。
7. 已上传 Worker 的显式发布不重建 D1、不重放 SQL/资产/秘密；部署漂移、流量分拆、域名冲突与日志超时均停止。21 项边界测试通过。
8. 首次公网验收发现 Cloudflare 统计脚本，两次外部请求导致失败；关闭仅邮箱专用 zone 的统计后，public-02 的 21 项全部通过。两个地址在 public-01 建立，后续复用。

## 质量命令（仓库根 PowerShell）

用仓库 .venv Python 执行：pytest tests/cloudflare_mail/test_mail_deploy.py；ruff format --check .；ruff check .；mypy packages/core/src apps/api/src apps/worker/src apps/mcp/src；mypy --explicit-package-bases infra/cloudflare_mail tests/cloudflare_mail/test_mail_package_build.py tests/cloudflare_mail/test_mail_deploy.py；pytest -m 'not integration'。

不重跑固定包的四轮构建或旧 29 项本地验收；新代码不改变包字节，已有验收边界维持。
