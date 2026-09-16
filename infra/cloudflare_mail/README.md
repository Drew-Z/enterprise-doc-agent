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

## 公网接入顺序（尚未执行）

1. 使用已盘点账号 2741446a7478f2d8a5ff31df7e077f17；为 docagent-private-mail 创建全新的 D1，选择并记录地区/所在地约束。不能复用 img_d1、dev 或 grok2api。
2. 将真实 D1 ID 写入此包的 wrangler.jsonc；先对新库执行 schema.sql，再执行 private-settings.sql。维持 routes=[] 和 workers_dev=false。
3. 上传尚无公共路由的 Worker，通过 secrets 注入三个独立密钥。缺少任一密钥时入口保持关闭。固定 site/admin 密码不等于 CF API Token，JWT_SECRET 不等于 Address JWT。
4. 为 inbox.ciallobill.ccwu.cc 设置 Worker custom domain；在 Email Routing Settings > Subdomains 为 mailtest.ciallobill.ccwu.cc 和 notify.ciallobill.ccwu.cc 分别完成配置。采用各子域的实际 DNS 要求，不复制根域记录或猜 MX priority。
5. 管理员通过受控入口建立测试邮箱；仅把这些精确收件地址的 Email Routing 规则指向本 Worker，不开 catch-all。首次公网检查仍使用合成内容。
6. 真正发信另行配置 SEND_MAIL 的明确 verified-destination 名单和发件域，并仅为 noreply 地址开启发送资格。需先明确获准的真实收件地址，再接现有 Keycloak 私有 SMTP/API 适配器。当前包未启用此步骤。

域名规划：inbox.ciallobill.ccwu.cc 为网页/API，mailtest.ciallobill.ccwu.cc 为测试收件域，notify.ciallobill.ccwu.cc 为身份发件域。当前没有这些公网邮箱，包也没有任何真实收件人或身份链接。

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
