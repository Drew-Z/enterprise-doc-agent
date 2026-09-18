# Cloudflare 私有邮箱包

此包固定 cloudflare_temp_email v1.12.0（39db6bad4377dfb3d1d67d71bd589597dff3c352）。初始仅收信，供 DocAgent 的合成试用邮箱使用。包构建与本地验收不代表公网部署、真实投递或 SaaS 商业验收。

## 内容与默认行为

- 编译 Worker、同源网页 assets、上游 MIT 许可证、D1 schema 和独立初始设置。
- PASSWORDS、ADMIN_PASSWORDS 使用 JSON 字符串数组，JWT_SECRET 使用独立字符串；每项至少 32 个 ASCII 非空白字符，角色之间不得复用。通过 Wrangler secrets 注入，不写入 vars。
- 缺少或无效配置时，HTTP（包括网页）返回固定 503，收信事件直接拒绝。配置有效后由上游分别核验站点密码、管理员密码、Address JWT。
- workers.dev、预览 URL、域名路由、定时任务、发信 binding、外部发送商、自动转发、自动回复、Webhook、AI 提取、公开建址均关闭。D1 ID 是全零占位符。
- 初始 SQL 关闭用户注册，拒收未知地址，verified_address_list 和无限发送列表为空。只对全新专用数据库执行；重复初始化会失败，避免覆盖已有设置。
- 上游邮件正文保存于 D1。正式保留期与处理地区尚未落实。上游含异常日志，此包关闭 Workers 持久日志与 source map 上传；不要在真实身份邮件处理期间启用 Tail/调试日志。
- 网页沿用上游的私有站点密码界面；静态资源不是机密。请求先经过私有配置检查，再交由上游处理。浏览器会保存其邮箱凭据，只在受控浏览器使用。

## 从源码构建（PowerShell）

在项目根目录运行，使用 Python 3.12+、Node 22+ 与 Corepack。工作目录和输出目录都必须不存在；不会覆盖已有内容。

~~~powershell
$buildRoot = Join-Path $env:TEMP ('docagent-mail-build-' + [guid]::NewGuid().ToString('N'))
$bundlePath = Join-Path $env:TEMP ('docagent-mail-bundle-' + [guid]::NewGuid().ToString('N'))
& ./.venv/Scripts/python.exe -B -m infra.cloudflare_mail.build --work-dir $buildRoot --output-dir $bundlePath
~~~

构建工具校验固定源码 archive/blob 文件哈希，用 Corepack 的 pnpm 10.10.0 冻结安装两份锁文件并禁用 dependency install scripts；不改全局 pnpm/Wrangler。Windows 不执行上游 POSIX 环境赋值脚本，直接用 Vite CLI。同源网页禁用 PWA 与 analytics token。构建子进程仅继承必要 OS 环境项，不继承 Cloudflare/产品/模型凭据。

上游 HTML 无条件加载 Turnstile，当前私有站点没有启用该功能。构建工具仅在核验后的工作副本移除这一个 script 标签，并在 manifest.json 记录修改前后的哈希；原始源码保留不变。浏览器验收检查登录及收件箱页面不发起外部请求。以后若启用 Turnstile，须连同前端脚本和浏览器验收重新配置。

manifest.json 记录所有部署文件的 SHA-256（排除其自身）和工具版本。构建日志保存在工作目录 logs；验收或故障排查完成后再按精确路径删除任务自有临时目录。

## 当前公网状态（2026-09-18）

网页/API 为 https://inbox.ciallobill.ccwu.cc/zh/。既有 Worker 与 D1 均为 docagent-private-mail，D1 UUID 为 8521706f-87c6-43af-8866-cc0ce95dd5d9；三组秘密保存在本机受保护文件中。首次 HTTPS 部署已完成，不能重跑首次初始化。

收件域已按用户要求复用开通 Email Routing 的 playarchive.eu.cc。两个地址 docagentowner01@playarchive.eu.cc、docagentmember01@playarchive.eu.cc 已建立，各有一条启用的精确规则指向现有 Worker。原域名 DNS、兜底转发及其他六域路由均保留。DMARC Management 不是收信开通页，Locked 不表示 Routing 故障。

新域 22 项、旧邮箱 21 项真实 HTTPS/API/Chromium 检查通过，全部 27 个线上 assets 与固定包哈希相同。原 owner01/member01@mailtest.ciallobill.ccwu.cc 凭据仍有效，但旧域未接入 Routing。当前是收件配置就绪，尚未做真实公网投递；发送功能仍关闭。详见 [本次接入报告](../../.trellis/tasks/09-18-saas-mail-routing-reuse/report.md)。

固定构建包仍保留原始 mailtest/notify 模板；本次通过已验证程序的运行时 overlay 更新 DEFAULT_DOMAINS 与 DOMAINS，保留旧两域，不改固定包、不重新初始化 D1。复用 Worker 时使用 keep_assets 与 keep_bindings=[secret_text]，随后严格回读唯一 active version 与私有边界。

## 重跑新域公网验收（PowerShell）

~~~powershell
$privateRoot = Join-Path $env:CODEX_HOME 'secrets\docagent-private-mail'
$publicEvidence = Join-Path $env:TEMP ('docagent-mail-public-' + [guid]::NewGuid().ToString('N'))
& node ./tests/cloudflare_mail/public-site.mjs --repo $PWD --credentials (Join-Path $privateRoot 'credentials.json') --mailboxes (Join-Path $privateRoot 'mailboxes-playarchive.eu.cc.json') --evidence $publicEvidence --domain playarchive.eu.cc --address-prefix docagent
~~~

凭据文件必须先受保护，证据目录必须不存在。省略新参数仍检查旧域。前缀只接受最多 22 个小写字母或数字；上游管理员建址也会去掉其他字符，连字符会改变实际地址。首次带连字符请求的失败记录已保留，通过既有管理员接口恢复本任务已创建邮箱的 JWT，未删除或重复创建。

真实发信仍需单独配置资格、明确获准的收件地址与 SEND_MAIL binding，再接 Keycloak 私有 SMTP/API 适配器；账号已有 verified destination 不代表允许自动发测试邮件。长期身份服务、完整产品会话、正文保留期与处理地区仍待后续验证。

## 官方依据

- https://developers.cloudflare.com/email-service/platform/pricing/
- https://developers.cloudflare.com/email-service/configuration/send-bindings/
- https://developers.cloudflare.com/email-service/configuration/subdomains/
- https://developers.cloudflare.com/workers/best-practices/workers-best-practices/

账号内已验证收件地址的 Worker binding 发送在所有套餐免费，包括仅配置 Email Routing 的情况；任意外部收件人需 Workers Paid。直接 Email Sending 子域接口此前返回 401，不能把已补齐的权限与该功能资格混为一谈。真实投递仍需单独验证。

## 构建与运行时约束

当前锁定 Wrangler 4.129.0 所用本地运行时实测最多支持兼容日期 2026-09-10。配置与本地验收统一固定该日期；使用 2026-09-16 的首次构建虽通过，运行时会拒绝启动，失败记录保留在任务证据中。后续升级兼容日期须连同运行时重测。TypeScript 5.4.5 来自冻结的间接依赖；构建工具直接调用其编译器，不使用主机上碰巧存在的 tsc。可用 --cache-dir 复用本任务已有的 pnpm/Corepack 缓存，源码仍逐文件重新校验，工作和输出目录仍须全新。

## 重跑本地验收（PowerShell）

在前述构建完成后、删除构建目录之前，从项目根目录执行：

~~~powershell
$mailLabRoot = Join-Path $env:TEMP ('docagent-mail-lab-' + [guid]::NewGuid().ToString('N'))
$mailEvidence = Join-Path $env:TEMP ('docagent-mail-evidence-' + [guid]::NewGuid().ToString('N'))
& node ./tests/cloudflare_mail/local-worker.mjs --bundle $bundlePath --toolchain (Join-Path $buildRoot 'upstream/worker') --work-dir $mailLabRoot --evidence-dir $mailEvidence --playwright-package (Join-Path $PWD 'apps/web/package.json')
~~~

浏览器依赖使用项目现有 @playwright/test 与 Chromium。验收通过本地模拟端点注入两封合成邮件，真实操作站点密码与邮箱凭据登录，验证邮箱隔离；不发送真实邮件。每次使用新的 D1 状态与证据目录，拒绝覆盖旧结果。finally 关闭浏览器、Wrangler 和 D1 proxy，只有检查及关闭均成功才标为 passed；报告保留失败阶段。合成密钥仅在该进程内，未写入部署包。
