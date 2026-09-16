# 私有邮箱部署包设计

## 组成

固定上游 dreamhunter2333/cloudflare_temp_email v1.12.0，commit 39db6bad4377dfb3d1d67d71bd589597dff3c352。源码选取 worker、frontend、db 和 LICENSE，通过固定 Git tree 验证每个 blob。冻结安装上游 pnpm lock，使用其本地 Wrangler 构建。主机全局 Wrangler 不升级。

infra/cloudflare_mail 持有小型私有启动检查入口、非秘密配置、构建脚本和运行说明。产物包含上游编译 Worker、同源前端 assets、D1 schema、入口和来源清单。上游原始源码保持不变。

## 私有入口

入口包装上游 fetch/email handler。PASSWORDS、ADMIN_PASSWORDS 必须为非空 JSON 字符串数组，JWT_SECRET 为独立强随机字符串；每项至少 32 个 ASCII 非空白字符。不合格时 HTTP 返回固定 503，收到的邮件被拒绝，异常不返回 secret 或正文。不实现新的邮箱登录协议；配置有效时仍由上游分别验证站点、管理员和地址凭据。

发送在本部署阶段显式关闭，未配置 SEND_MAIL、外部供应商 key 或自动转发；默认地址发送余额为 0。新包不发送真实邮件。后续绑定名单需以用户明确批准的 verified destination 为准。

## 配置与数据

规划 Worker 名 docagent-private-mail，D1 名 docagent-private-mail，前端/API 同源 inbox.ciallobill.ccwu.cc。workers_dev=false，preview_urls=false，routes=[]，无定时任务或自动清理地址。数据库仅规划新资源，不复用已有 img_d1、dev 或 grok2api。

DOMAINS 包含 mailtest.ciallobill.ccwu.cc 和 notify.ciallobill.ccwu.cc，关闭用户建址、自动回复、Webhook、AI 提取和公开用户注册等可选能力；管理员手工建立受控邮箱。上游将收发正文写入 D1，正式部署的保留期和处理地区仍须落实。

本地 D1 使用临时目录及明确的 local 模式，与云资源 ID、数据库文件完全分离。假邮件只在本地模拟。网页构建使用空 VITE_API_BASE 走同源，不含真实 secrets。

## 验证与恢复

先对启动检查公共 handler 写失败测试，再实现，并用实际上游 Worker 的本地 HTTP/D1 验证成功和拒绝路径。仅记录状态、计数、布尔检查及无敏感值截图，凭据只在临时运行配置或内存中。

所有本轮临时源码、安装依赖、模拟数据库、日志和进程记录在 task 临时根；必要证据及构建包复制到任务目录后，核实范围并精确删除。未修改的既有文件、镜像、其他 worktree 和原验收记录不回收。

## 实施中核实的运行时接口

- 当前 Wrangler schema 支持 secrets.required；只列三个秘密的名称即可生成 Env，不需要为类型生成写临时秘密值。
- 锁定 Wrangler 4.129.0 的运行时拒绝兼容日期 2026-09-16，明确报告最新支持 2026-09-10。因此部署和本地配置固定为 2026-09-10，升级时重测。此处根据实际固定依赖收紧通用的“使用今天日期”建议。
- 实际本地邮件模拟路径为 /cdn-cgi/local/email。getPlatformProxy 仅用于初始化/核对本地 D1；实际 HTTP、收信和 Chromium 通过 unstable_dev 的真实 workerd 运行。remoteBindings=false、local=true、forceLocal=true、D1 remote=false，数据库持久路径显式统一到该轮临时 state/v3。
- 从冻结依赖图定位唯一的 TypeScript 5.4.5 编译器；上游没有根级 tsc 命令 shim。
- 真实浏览器发现上游 frontend/index.html 无条件加载 Turnstile，即使未配置 captcha 也发生外部请求。只在已核验的构建副本移除这一固定 script 标签，要求唯一匹配，并将修改前后 SHA-256 写入 manifest；原始上游源码与依赖锁仍保持不变。私有入口与收件箱验收要求外部请求数为零。
