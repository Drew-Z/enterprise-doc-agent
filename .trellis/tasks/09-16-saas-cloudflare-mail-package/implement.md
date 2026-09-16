# 实施与验证

## 顺序

- [x] 核实账号权限、资源清单与上游固定 tree，建立工作区基线。
- [x] 完成 PRD/设计与上下文，按用户“继续”授权实施。
- [x] 编写配置缺失拒绝的行为测试，记录失败；实现私有启动检查并转绿。
- [x] 制作非秘密配置及可重复构建脚本，冻结依赖并构建 Worker 与前端。
- [x] 用真实本地 Worker/D1 验证站点及管理员拒绝、地址创建、收信与隔离。
- [x] 检查浏览器入口，生成包与文件哈希、部署说明和来源记录。
- [x] 运行适用质量检查、核对历史文件保护、清理本轮临时资源并更新任务元数据。

## 行为切片

公共接口是 private mail handler 的 fetch/email；外部依赖为上游 handler，快速测试注入计数 handler，真实集成使用固定上游 Worker 与本地 D1。配置缺失时应 503 或 setReject，且不能调用上游。有效配置才委托上游；认证由真实上游集成检验。模拟邮件边界是本地 Workers 运行时，不是公网 MX。

## 检查

- Node test runner 检查私有入口，node --check 验证 JS 语法。
- Wrangler types / deploy --dry-run 验证绑定和打包；pnpm frozen-lockfile 安装，前端 Vite 构建。
- 新 Python 构建代码遵循 Ruff/mypy，运行实际构建及输入保护检查；应用公共后端质量门禁按实际变更范围执行。
- 本地 Worker HTTP/D1 行为报告与必要页面截图。
- JSON/链接/secret 扫描、git diff --check、基线文件哈希、HEAD/index 保护。

## 尚未执行的公网步骤

本轮没有 deploy、d1 create --remote、DNS write、token policy write 或邮件发送。正式接入前须取得独立 D1 ID、配置 secrets 和邮件子域，最后开放域名；真实试发需要用户明确收件人。

## 实测结果

最终使用 build-04 / run-04：29 项真实本地检查通过；浏览器 0 次外部请求、0 个 pageerror；D1 为 2 个地址、2 封合成信、0 条发送。三个最终页面截图已实际查看。

私有入口 16 项行为测试通过，保留配置缺失与上游错误正文处理的红绿日志。构建保护 10 项通过，包含在本轮后端 1335 passed / 312 deselected 总数中。Ruff 510 文件通过；应用 mypy 219 文件及邮箱/构建测试 3 文件通过。Python namespace 检查使用 --explicit-package-bases。

运行时兼容日期与 Turnstile 外链问题均保留失败证据和修复后的实际结果，详见 README.md。新增 code-spec 已覆盖命令、秘密、错误矩阵、数据/浏览器路径及资源生命周期。旧基线 Windows 缓存变化单独记录，不能声称全部旧文件字节未变。
