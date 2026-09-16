# 固定邮箱包的来源与实施依据

## 已核对来源

- 上游 v1.12.0 / 39db6bad4377dfb3d1d67d71bd589597dff3c352：source-verification.json 的 210 个文件已逐个对固定 Git tree 校验 blob SHA-1 与 SHA-256。infra/cloudflare_mail/source.lock.json 持有可重建锁；上游原件没有修改。
- Workers 最佳实践： https://developers.cloudflare.com/workers/best-practices/workers-best-practices/ ，本轮用 smart-search fetch 取得正文，采用生成 Env、使用 secrets、完整等待 Promise、显式错误处理等要求。上游正文/错误日志的限制单独写入 README；不为身份邮件开启持久日志。
- 当前 Workers types 5.20260916.1：从 npm metadata 的固定 URL 读取压缩包，SHA-512 与 registry integrity 相符；读取 ForwardableEmailMessage.raw/rawSize/setReject 及 ExportedHandler 的 fetch/email 签名。此项只作当前 API 参考，实际构建依赖仍固定为上游锁。
- Wrangler 4.129.0 实际安装的 config-schema.json：支持 secrets.required、assets.run_worker_first、D1 remote=false、workers_dev、preview_urls、observability。完整 schema 随包放入 provenance。
- 本地邮件模拟： https://developers.cloudflare.com/email-service/local-development/routing/ 。当前入口为 POST /cdn-cgi/local/email?from=...&to=...，请求体是含 Message-ID 的 RFC 5322 邮件。来自 email-service/llms.txt 的当前链接，未采用旧 /cdn-cgi/handler/email。
- 本地 API：实际 Wrangler cli.d.ts 中的 unstable_dev 与 getPlatformProxy。proxy 的 persist.path 要指向 state/v3，dev 的 persistTo 指向 state。proxy 明确 remoteBindings=false；dev 明确 local=true/forceLocal=true。

## 运行中查明的差异

首次源码构建通过后，pnpm exec tsc 失败，因为上游没有根级编译器 shim。固定依赖内的 TypeScript 5.4.5 可直接执行，私有入口类型检查通过。

第一次真实运行失败于兼容日期：固定运行时明确最多支持 2026-09-10。将交付配置统一固定为该日期，保留初次失败报告；不能把 dry-run 构建成功当作运行成功。

第二轮真实浏览器完成登录与收件箱后，零外部请求检查发现两次请求。第三轮补充仅记录 origin 的诊断，确认均来自 https://challenges.cloudflare.com。上游 frontend/index.html 第 18 行无条件加载 Turnstile，即使当前未启用 captcha 也会请求；只在构建副本移除唯一固定标签，并记录修改前后哈希。第四轮真实验收通过，外部请求与 pageerror 均为零，D1 保留两个地址、两封合成信、零发送记录。前三轮失败证据保留。

将 Python namespace package 与它的测试同时交给 mypy 时，需要 --explicit-package-bases；否则同一模块会被分别识别为 cloudflare_mail.build 和 infra.cloudflare_mail.build。没有为此添加全局配置或忽略类型错误。

上游私有站点密码缺失时返回空列表，HTTP 中间件仅对非空列表校验；新 private-handler 在 HTTP/收信入口前验证私有配置。上游仍负责站点/管理员/地址认证和 D1 读写，不新写第二套邮箱协议。

## Cloudflare 当前只读复核

最新观察详见 cloudflare-access-current.json（2026-09-16T03:00:57.332Z）。Token active；策略可读且包含 D1、Workers、邮件路由/地址和 Email Sending Write；Account/Zone 范围为 wildcard，任务操作限定既定账号/zone。D1/KV/路由读取成功，已验证 destinations 2 个（只保留掩码），没有选为获准真实试发对象。Email Routing 仍 disabled/unconfigured；文档化 Email Sending 子域接口仍为 401/code 2036，不能再归因于缺少已存在的 Email Sending Write。没有执行任何 CF 写操作。

01:39 与 01:44 的前序观察由此次可复核快照接续；01:32 native followup 文件保持历史原样。当前 Token 验证路径正确记录为 /user/tokens/verify。

## 后续真实接入的邮件范围

已核对官方定价： https://developers.cloudflare.com/email-service/platform/pricing/ 。账号内已验证收件地址的 Worker binding 发送在所有套餐免费，包含仅配置 Email Routing 的场景；任意外部收件人需要 Workers Paid。发送限制见 https://developers.cloudflare.com/email-service/configuration/send-bindings/ 。当前包不含 SEND_MAIL，不推断直接 Email Sending 资格。子域逐个配置规则见 https://developers.cloudflare.com/email-service/configuration/subdomains/ 。

正式投递、邮件保留期、D1/Keycloak/模型处理地区、长期 Keycloak 托管和完整产品 HTTP 登录仍待完成。上述本地验收不产生真实客户、试用团队或付费成绩。
