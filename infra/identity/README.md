# Keycloak 与 Cloudflare 私有邮件

使用 `cloudflare_temp_email v1.12.0` 管理私有测试收件箱，Keycloak 负责统一登录。计划中的身份邮件链路为 Keycloak → 同机 SMTP 适配器 → 邮箱 API → Cloudflare 原生 binding，无需另购 SMTP 服务。

截至 2026-09-18 的项目证据：私有邮箱 HTTPS 与 `playarchive.eu.cc` 两条精确收件路由已部署，用户发送的公网测试邮件已完成入库和邮箱隔离验证；发信仍关闭。真实 Keycloak 已与完整产品首次使用流程在本地贯通，包括邮箱验证和密码重置；长期 HTTPS 身份托管及公网身份邮件尚未验证。公网收信证据见[域名复用交付](../../.trellis/tasks/archive/2026-09/09-18-saas-mail-routing-reuse/report.md)，不能用它推断发信已开通。

## 运行本地验证

在仓库根目录用 PowerShell 执行。依赖现有 Python 环境、Web Playwright/Chromium 和 Docker Desktop；两个镜像已固定到 `linux/amd64` digest。

```powershell
docker pull quay.io/keycloak/keycloak@sha256:26939e1318d6f008fc2ee6e10cec1cf8f1ba8a21846c1bc81b91ed0506bc2a7a
docker pull ghcr.io/dreamhunter2333/cloudflare_temp_email/smtp_proxy_server@sha256:02146a8d21a5c4488e7217bb3dc065eaccc7b1cecab27019c7dcd0c33866bb3a
& .\.venv\Scripts\python.exe -B -m infra.identity.local_lab --evidence-dir "$env:TEMP\docagent-identity-check-$([guid]::NewGuid().ToString('N'))"
```

脚本创建独立 Compose project、随机端口和随机凭据，运行真实 Keycloak 浏览器流程与产品现有 `OidcClient`，最终删除本次容器和网络。H2 数据、邮件、密码与链接随临时容器回收；报告和页面截图保留在指定目录。不会修改产品 `.env` 或 PostgreSQL。镜像缓存保留以便下次运行。

报告只有在流程完成、容器/网络回收且回调监听器关闭后才记为 passed；清理失败会记录 failed。最终验收与独立的清理失败回归见[本轮交付](../../.trellis/tasks/09-16-saas-native-mail-identity/README.md)。

本地链路是 **真实 Keycloak → 真实 SMTP → 私有 HTTP API 捕获器**。捕获器替代 Cloudflare 网络边界，不验证公网投递、Worker/D1、MX 或收件箱页面。截图不含地址栏、授权码或明文密码；测试不保存 HTTP trace。该检查没有启动完整产品 Web/API 或验证产品数据库会话。

`lab.compose.yaml` 用于独立身份检查和下面的完整产品检查；Keycloak 使用 `start-dev`。普通 Docker bridge 的所有发布端口都限制在 `127.0.0.1`。本机 Docker Desktop 的 internal bridge 未实际发布端口，因此不使用它来伪造可访问的隔离环境。

## 验证完整产品首次使用

使用仓库已有 PostgreSQL、MinIO、Redis 和固定 Keycloak 镜像，并确认 5173、18769、18770 空闲。先准备一个新的绝对证据目录，再执行：

```powershell
$env:FIRST_USE_OUTPUT_DIR = '<已存在且未运行过的绝对证据目录>'
pnpm.cmd --filter web exec playwright test -c playwright.keycloak-first-use.config.ts
```

该场景通过生产 `create_app(settings=...)`、真实 Keycloak、SMTP 和本地邮件捕获器完成两名用户验证、两家企业准入/邀请、三格式上传、真实 Worker 处理、售前生成与复核/CSV、用量/隔离/退出，再验证密码重置、旧密码拒绝和原产品身份保留。模型与 embedding 为本地受控实现，邀请仍由管理员手动分享。

Keycloak 使用 `localhost`，产品使用 `127.0.0.1`，并实际检查产品 cookie 没有传给身份服务。证据回读真实身份事件与数据库绑定；不能以签名测试 IdP 的计数替代。最终 `cleanup.json` 和 `keycloak-cleanup.json` 必须同时通过，测试只回收自己的 schema、对象、Redis 键及 Compose 资源。密码、邮件正文和 action URL 不写入证据；自动 trace、视频和失败页面快照关闭。

本轮结果与重现入口见[完整身份旅程交付](../../.trellis/tasks/09-18-saas-keycloak-product-journey/report.md)。原 `playwright.first-use.config.ts` 保留签名测试 IdP 场景，两套浏览器场景不得同时运行。

## 已固定的上游

- 邮箱源码：[v1.12.0 / 39db6bad](https://github.com/dreamhunter2333/cloudflare_temp_email/tree/39db6bad4377dfb3d1d67d71bd589597dff3c352)。镜像内 SMTP 源码 SHA-256 与该 commit 一致；依赖为 aiosmtpd 1.4.6、httpx 0.28.1。
- 身份服务：[Keycloak Docker 指南](https://www.keycloak.org/getting-started/getting-started-docker)，版本 26.7.0。

原 SMTP 代理有两项不适合这套私有配置的行为：没有传递 `x-custom-auth`，且 INFO 日志写入完整正文。`mail_relay.py` 使用上游镜像里的协议依赖，独立实现身份邮件的窄适配；不启动原代理或 IMAP，不修改上游 Worker。

## 接入已部署的私有邮箱

`cloudflare-relay.compose.yaml` 是给后续真实接入准备的适配器配置；它只接入已有 Keycloak 私有 Docker 网络，不发布 SMTP 端口。当前没有运行它，账号实际发信资格及获准收件地址仍待核实。以下 `notify` 发件参数是原计划，不是已启用的发送配置；实际接收域已改用 `playarchive.eu.cc`。

| 配置 | 含义 |
| --- | --- |
| `IDENTITY_PRIVATE_NETWORK` | Keycloak 已连接的私有 Docker 网络名 |
| `IDENTITY_SMTP_PASSWORD` | 独立随机 SMTP 密码，至少 32 个 ASCII 非空白字符 |
| `CLOUDFLARE_MAIL_SITE_PASSWORD` | 邮箱 `PASSWORDS` 列表中的一个站点密码，至少 32 字符 |
| `CLOUDFLARE_MAIL_ADDRESS_TOKEN` | 固定发件地址的 Address JWT；不是 Cloudflare API Token，也不是 JWT_SECRET |
| `CLOUDFLARE_MAIL_VERIFIED_RECIPIENTS` | 经 CF 实际验证且获准测试的收件地址 JSON 数组；空数组被拒绝 |

凭据留在本机私有 secret 配置中。不要将带真实值的 `docker compose config` 输出写入报告。

Keycloak 邮件设置：host `mail-relay`，port `8025`，发件人和 SMTP 用户名均为 `noreply@notify.ciallobill.ccwu.cc`，开启认证，密码为 `IDENTITY_SMTP_PASSWORD`。SSL/STARTTLS 关闭仅适用于这里的同机私有 Docker 网络；该配置没有面向公网的 SMTP 入口。适配器到邮箱 API 使用 HTTPS。

适配器只向固定 `/external/api/send_mail` 发请求，附上站点密码和地址 JWT；每封只允许一个明确收件人，正文上限 256 KiB，HTTP 总期限 10 秒，响应上限 4 KiB。失败使用固定 SMTP 文案，不回显上游正文、不重试，不记录 SMTP/HTTP 库日志。成功表示邮箱 API 明确接受请求，不表示收件方已最终投递。

## Cloudflare 免费发送的前提

[官方定价](https://developers.cloudflare.com/email-service/platform/pricing/)明确：给账号内已验证的 destination addresses 发信，在所有套餐均免费；向任意外部地址发送才需要 Workers Paid。当前选择前一条路径，不自动开通付费计划。官方 SMTP 465 是另一条接法，其启用和套餐条件没有在本账号验证，不能把 Worker 免费路径直接套用到 SMTP。

必须区分三个状态：

| 状态 | 谁确认 | 用途 |
| --- | --- | --- |
| Cloudflare verified destination | Cloudflare 的验证邮件流程 | 决定能否使用免费发送范围 |
| 邮箱项目 `verifiedAddressList` | 私有邮箱管理员配置 | 选择项目的兼容发送分支；不能替代 CF 验证 |
| Keycloak `email_verified` | 用户实际完成 Keycloak 邮件验证 | 产品 OIDC 接受身份所需声明 |

`owner01@mailtest.ciallobill.ccwu.cc` 与 `member01@mailtest.ciallobill.ccwu.cc` 目前只是规划和本地合成身份。它们尚不是 CF verified destinations；是否能作为目标地址完成 CF 验证，必须由账号实际检查，不能靠手工填写名单推断。不会自动向 CF 账号邮箱发测试邮件。

在锁定上游 Worker 配置中，收件域与发件域都要在 `DOMAINS` 内：`mailtest.ciallobill.ccwu.cc`、`notify.ciallobill.ccwu.cc`。使用名为 `SEND_MAIL` 的 send_email binding，设置 `allowed_sender_addresses` 为固定 noreply 地址、`allowed_destination_addresses` 为已核实目标列表；具体限制见[官方 binding 配置](https://developers.cloudflare.com/email-service/configuration/send-bindings/)。

继续关闭匿名创建、自动回复与自动清理测试地址，开启地址密码。保持 `DEFAULT_SEND_BALANCE=0`；管理员只为固定 noreply 地址启用正余额。v1.12.0 在进入 verifiedAddressList 分支之前检查发送资格，所以“免费发送”不等于地址自动获得发信权限。项目的余额/额度是 soft guard，不是费用硬上限。

`notify` 发送域必须完成 Cloudflare onboarding；`mailtest` 接收域需要自己的邮件 DNS 与路由。MX/SPF/DKIM/DMARC、D1 ID、页面与身份入口的目标值应从实际开通结果取得，本交付不编造这些值。

上游 Worker 会将已发邮件正文写入 D1 `sendbox`，验证与重置链接也包含其中。适配器不写正文日志不等于邮箱没有存储；真实接入时需要为私有邮箱明确访问范围、邮件保留期和处理地区。本轮只使用内存捕获器，没有创建 D1。

## 当前账号接入缺口

早期 Routing/destination 读取失败已被后续权限核验、真实部署和收信结果补充，详见[权限检查记录](../../.trellis/tasks/09-16-saas-cloudflare-mail-package/cloudflare-access-current.json)及[公网收信记录](../../.trellis/tasks/archive/2026-09/09-18-saas-mail-routing-reuse/delivery-verification-2026-09-18-02.json)。保留这些独立证据，不把旧 403 当作当前没有邮箱能力的结论。

Email Sending 接口的最后记录仍为 401，实际发送资格、发送域 onboarding 与获准目标地址没有通过实测。下一步需对照账号现状完成发送配置和明确收件范围，再接入长期 Keycloak；当前 `cloudflare_mail_public_send_verified` 与 `cloudflare_native_delivery_verified` 均为 false。公网收信和本地身份验证不补足这两项证据。

统一身份的产品参数示例见 [keycloak.example.env](keycloak.example.env)，默认关闭。正式托管需要稳定 HTTPS issuer、精确 confidential client 和数据库；本地 Keycloak 验证不能代替这些配置，也不继承历史 AWS 新加坡的数据地区保证。
