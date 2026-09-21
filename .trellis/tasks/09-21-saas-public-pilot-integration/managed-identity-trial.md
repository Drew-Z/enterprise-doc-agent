# 托管认证接入尝试

用户已选择优先尝试 Cloudflare Access 和 Supabase Auth。当前先接 Access；不运行自托管
Keycloak，不连接两台新增服务器，不修改现有业务服务器配置。

## 2026-09-21 当前结果

用户选择现有团队内独立应用和策略后，`DocAgent public pilot` 已创建并回读核验。
元数据、JWKS 和验证码页均可达。用户随后批准两个测试邮箱各一次验证码，两封均已收到。
首次自动化提交后未捕获产品回调，尚未执行授权码兑换或验证真实身份声明，新版产品
也未发布。用户另批准 owner 补发一次，亦已送达。已复现并修正测试程序漏掉重定向后回调
的问题；修复后的两个邮箱各一次补测正在等待新的发送授权。合计已发送并收到三封邮件。

| 检查 | 结果 | 能说明什么 |
| --- | --- | --- |
| Cloudflare 当前 API token | active，账户 ID 与现有邮箱一致 | 可继续复用现有账户，不等于所有 Access 权限已具备 |
| Access 应用、身份源 | 权限更新后读到既有 Warp 应用和 OTP IdP；随后创建并核验独立项目应用 | 原有资源未变，项目使用独立策略 |
| Zero Trust 组织配置 | 权限更新后 HTTP 200，团队为 `billyciallo.cloudflareaccess.com` | 原组织读取 403 已解除 |
| Cloudflare 账户订阅 | 403/code 10000 | 未确认账户套餐或费用，不自动开通付费项 |
| Supabase 项目 | `wkbyfxoawitbkitckipy` / `biau-internal-assistant-db`，ACTIVE_HEALTHY | 现有健康项目，是否允许复用仍需确认 |
| Supabase 原生 Auth 设置 | email=true，disable_signup=false，mailer_autoconfirm=false | 邮箱认证启用，未测试发送或确认验证码 |
| Supabase discovery | 200，支持 code、S256、client_secret_basic、nonce/email_verified claims | 描述协议能力，不证明功能可使用 |
| Supabase 公开 JWKS | 当前一把 ES256 公钥 | 本项目应显式选择 ES256，不能仅检查 RS256 |
| Supabase 授权端点 | 404，`feature_disabled`，`OAuth server is disabled` | 当前不能直接供产品已有授权码客户端登录 |

账户和 Supabase 探测为只读请求；Supabase OAuth 探测使用不存在的合成 client ID，没有
创建账号、授权、发信或改动共享项目。Access 的获准创建操作单列如下。初次检查组织返回
403、应用/身份源为空，用户补充权限后的观察取代了该初始结果。

## Access 已创建并验证的配置

用户更新权限后已重新读取成功：team domain 为 **`billyciallo.cloudflareaccess.com`**。
此时能看到一个既有 `Warp Login App` 和一个 `onetimepin` 身份源；本任务没有创建或修改
它们。用户随后明确选择“现有团队内独立应用和策略”，创建 API 返回 201，回读验证通过。
原组织读取 403 已解除，订阅查询仍为 403；没有购买或升级套餐。

一个 Cloudflare 账户对应一套 Zero Trust 组织。另建独立团队需要另一 Cloudflare 账户；
产品域名、DNS 和邮箱可以留在原账户，OIDC 通过新团队 issuer 集成，无需迁移它们。

| 项目 | 实际值 |
| --- | --- |
| Cloudflare account | `2741446a7478f2d8a5ff31df7e077f17` |
| Application | `DocAgent public pilot`，类型 SaaS / Generic OIDC，ID `348ba6aa-60a0-4bd6-b9df-882b9ffaefe9` |
| Policy | 独立 allow policy，ID `16ceeda9-0eab-49d0-8e7b-64a9cbc803e8` |
| Client ID | `ed1c238b649b6fd566967970aedd187733d828463d32484cd7199112f8d5a92d` |
| 登录方式 | One-time PIN |
| 回调 | `https://agent.playlab.eu.cc/auth/callback`，不使用通配符 |
| OAuth | Authorization Code、PKCE S256、confidential client |
| Scope | `openid email` |
| 试验允许名单 | `docagentowner01@playarchive.eu.cc`、`docagentmember01@playarchive.eu.cc`，仅两个完整地址 |
| 当前邮件授权 | 两地址各一次和 owner 单次补测均已执行，三封均收到；修复捕获后的各一次补测等待新答复 |
| 产品权限 | 通过原有准入绑定及企业成员关系授予，不按邮箱自动创建企业或合并身份 |

组织读取及应用写权限已通过真实操作验证，不需再次更新权限或新建团队。应用只选现有 OTP
身份源，`grant_types=["authorization_code_with_pkce"]`，client secret 必需，会话 8 小时，
不显示在 app launcher。创建前配置快照在集中恢复组；新 client secret 仅在本机受保护的
`D:/Agent/codex/secrets/docagent-managed-identity/cloudflare-access-client.json`，尚未写入服务器。

Access 的邮箱 PIN 与现有 Cloudflare 收件箱是两个组件。PIN 由 Access 发出，现有两个
收件箱用来接收；无需为此把收件箱改成发信服务。仍须真实验证 ID token 的 email_verified、
nonce、稳定 sub、签名及客户端换码方式，不能因为官方列出通用 claims 就假设 OTP 已满足。

`cloudflare-access-application-draft.json` 是创建时采用的请求草案，执行结果在
`cloudflare-access-create.json`，状态为 `created_verified`。**不要重复执行 create**。
公开配置在 `cloudflare-oidc-profile.json`，issuer 为团队域名加
`/cdn-cgi/access/sso/oidc/<client_id>`；授权、token、JWKS 分别在其下的 `/authorization`、
`/token`、`/jwks`。官方请求字段来源为 `access-application-setup.json`。

实际 discovery 省略 `code_challenge_methods_supported`，客户端认证字段为
`token_endpoint_auth_methods`。Smoke 仅对精确 Cloudflare issuer/client/PKCE-only flow
兼容缺项并记录 `metadata_notes`；应用仍发送 S256，不接受 plain-only 或 post-only。
JWKS 返回两把 RS256 签名公钥。真实应用生成的授权 URL 返回 302 后到达 HTTP 200 验证码页。

本机默认代理请求曾连接失败，httpx 直连成功；Chromium 直连并关闭 HTTP/2、QUIC 后打开
验证码页。仅使用局部测试进程参数，没有改变系统代理或关闭 TLS 验证，不推断其他用户
浏览器同样失败。入口回执为 `cloudflare-authorization-http-entry.json` 和
`cloudflare-authorization-browser-http1.json`。

发信前两个收件箱 GET API 均返回 200，owner 有 1 封已有邮件、member 为 0，回执为
`managed-identity-mailbox-readiness.json`。后续三封验证码已到达各自收件箱；不保存正文、
不修改或删除邮件。最初普通路由拦截未覆盖重定向后的请求，未能证明回调被本地截获。
已用 CDP Fetch 在每个请求阶段限制来源并阻止产品 origin，同时从 302 Location 在内存
中获取回调。真实换码仍须使用 `OidcClient.exchange()`，不绕过 nonce/email_verified。

## 首次获准真实验证码试验

- 用户明确回复“可以”，批准两个测试邮箱各发送一次。owner 于 14:09:56 UTC、member
  于 14:10:46 UTC 分别触发一次；均在数秒后读到新的 Cloudflare 验证码邮件。
- 提交验证码后未观察到产品回调，两次均为 `callback_not_observed`。首次程序没有保存
  该阶段的页面错误信息，因此无法据此确定根因；也未进入应用授权码兑换或声明验签。
- 仅使用已发邮件中的登录链接尝试恢复，未额外发信。两者均显示 Cloudflare Error 页及
  `Request New Code` 按钮；新授权请求又显示邮箱登录页，未获得可用的登录会话。
- 账户认证日志的只读请求返回 403/code 10000。另一次读取既有链接的 HTTP 诊断遇到 TLS
  握手超时；未把网络错误当作身份不兼容的证据。首次失败原因仍未确定。
- 原 PIN 按官方说明 10 分钟有效。用户明确批准 owner 额外一次，14:38:10 UTC 已触发并
  收到。诊断观察到 POST `/cdn-cgi/access/callback` 返回 302，随后 OIDC authorization
  再返回 302；原回调变量仍为空。没有证据表明验证码重复提交是失败原因。
- 回执为 `cloudflare-real-login.json`、`cloudflare-real-login-link-recovery.json` 和
  `cloudflare-authentication-logs.json`、`cloudflare-real-login-owner-retry.json`；复用脚本
  均在本任务目录，发送模式有独占回执和对应授权标记检查。验证码、授权码、cookie 和
  token 没有写入报告或 Git；这不等于已经证明旧版 Web 从未收到回调请求。

## 回调捕获修复与剩余验证

本地两个 HTTP origin 的真实 302 实验复现：Playwright `context.route` 回调只看到初始
请求，重定向后的产品回调不会进入该处理器，合成 code 仍到达目标服务器。早期回执里的
`product_callback_intercepted_locally=true` 是未经验证的意图标记，已通过附加更正说明
修正，不能继续当成实际保证。此测试缺陷足以解释为何未捕获回调，不能据其判断 Access
不支持登录，也未证明那些旧请求最终包含成功授权码。

第一次改用 Network.setBlockedURLs 的实验仍让回调到达目标，失败回执保留；改为
CDP Fetch 的 request-stage 拦截后，本地真实重定向中捕获成功且产品 origin 请求数为 0。
最终白名单及验证码单次 POST 保护也由相同请求边界执行。证明见
`cloudflare-callback-capture-check-enforced.json`，前两次中间实验分别保留在其它
`cloudflare-callback-capture-check*.json`，不把本地模拟输入记为真实身份验收。

修复后的执行模式为 `--send-approved-capture-retry`，仅在 task.json 明确记载新的两个
邮箱各一次授权且本地拦截检查通过后运行。独占回执为 `cloudflare-real-login-capture-retry.json`；
该回执尚不存在，尚未触发这两封新邮件。新的授权问题已发出，不重复询问或自动发送。

用户提出两次失败可能源于网络延时。现有收件时间显示前两封在约 4–6 秒内到达，首次
验证操作在一分钟内，不能据此归为超出 PIN 有效期。另有单独 TLS 超时证据，保留其影响
待验证。已为收信、提交、回调和换码增加时间戳/耗时及脱敏网络错误；已发送请求的页面
跳转超时后继续检查原收件箱，仅对短暂失败的 GET 做有界重试，不重发邮件或重放授权码。
收码、提交和换码始终在同一浏览器会话连续执行，下一轮以分阶段证据判断根因。

Google 登录可作为标准 OIDC 提供商；普通 GitHub OAuth 直接接入需要适配，也可先接入
Access 再由其统一提供 OIDC。新增这些方式仍要配置对应客户端和允许策略，产品的企业
准入、成员关系及会话权限不由第三方登录自动替代。用户当前仅询问可行性，未切换路线。

## Supabase 第二路线

原生 `signInWithOtp` / `verifyOtp` **不依赖 OAuth server 开关**，可作为另一条实现路线，
但需要为本产品增加验证码入口和服务端身份适配。不可把 Supabase session 直接当作本产品
企业会话；仍需已确认邮箱、稳定 issuer/subject 和原有准入/成员关系。

若要复用已实现的 OIDC 授权码登录，则需启用 OAuth 2.1 server、注册 confidential client，
并实现登录及 authorization/consent UI。官方资料称该功能当前 beta 期间各计划免费；这是
文档信息，不是未来定价承诺。授权页面使用项目全局 Site URL + authorization path，不能
覆盖 `biau-internal-assistant-db` 可能正在使用的 URL/邮件设置。

当前邮件是否配置 custom SMTP 未取得管理接口证据，不能断言这个项目正在使用默认 SMTP。
若使用默认 SMTP，官方限制收件人为项目组织团队成员、默认约 2 封/小时；对真实客户应配置
正式发信服务或 Send Email Hook。现有 CF 收件箱的收信能力不能替代这一发送条件。

## 发布与验收

本轮为发布生成器、匿名登录检查及 staging workflow 增加显式托管 OIDC 配置。
`STAGING_BROWSER_AUTH_OIDC_CONFIG` 只包含公开端点与允许的 RS256/ES256；客户端密码
不进入此变量。原 Keycloak 参数仍兼容，但不是本轮部署目标。

两家都还没有完成真实登录。Access 实际邮件投递已通过，验证后的回调和换码尚未完成；Supabase 需明确共享项目
使用边界，并选择原生 OTP 适配或 OAuth server/UI。新版业务源码还没有远端发布，线上
v0.1.34 的 HTML 回退问题必须随新业务镜像发布解决。元数据、账户探测、本地测试均不能
代替“登录 → 上传 → 响应 → 引用核验/复核 → CSV 导出”的公网验收。

## 来源

- [Cloudflare 组织读取权限](https://developers.cloudflare.com/api/resources/zero_trust/subresources/organizations/methods/list/)
- [Cloudflare Generic OIDC SaaS](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/saas-apps/generic-oidc-saas/)
- [Cloudflare One-time PIN](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/)
- [Supabase OAuth server 入门](https://supabase.com/docs/guides/auth/oauth-server/getting-started)
- [Supabase 邮件 OTP](https://supabase.com/docs/guides/auth/auth-email-passwordless)
- [Supabase SMTP 限制](https://supabase.com/docs/guides/auth/auth-smtp)

官方 OAuth server 抓取原文保存在 `identity-supabase-oauth-setup.json`。

## 本轮本地验证与限制

- 格式化后最终发布配置、匿名入口、发布记录和 Secret 校验四个文件共 154 项测试通过；
  本阶段此前的 workflow 合约另 8 项通过。最终测试输出为 `managed-identity-pytest-final.txt`。
- Ruff check/format 通过，Mypy 两个修改脚本通过；结果在对应 `managed-identity-*-final.txt`。
- `single-node-4c4g` 实际 Kustomize 渲染 25 个对象；使用现场 Supabase 端点和合成 client ID
  通过脚本 CLI 配置及 BrowserAuthSettings staging 校验。未生成真实 Secret，未部署。
- 工作流 YAML 解析、21 个 Bash 片段的纯语法检查通过；dispatch 输入仍为 10 项。
- 本轮未完成 actionlint：本机没有现成可执行文件，官方二进制下载分别遇到连接错误与超时。
  上述 YAML/Bash/合约检查不冒充 actionlint 结果，后续发布仍应补齐该检查。
- 复用集中恢复组的 `managed-identity` 阶段，7 个文件恢复点和两份 Cloudflare 私有恢复
  材料已重新核对哈希。已完成获准的 Access 应用/策略写入及三封验证码发送；无提交、
  推送、业务部署或数据库修改。之前本地接入阶段的复核及清理结果保留在 managed-identity
  回执中；真实试发以新的 cloudflare-real-login 回执为准。
