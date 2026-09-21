# 公网试点：登录与业务发布

目标是让获准的试用者在正式网页登录，上传资料，生成并复核售前响应，导出 CSV。
以下准备基于 2026-09-21 的现场检查及获准的 Access 配置；它不是公网试点已上线的回执。

## 现状与部署顺序

- `https://agent.playlab.eu.cc` 及 readiness 正常，五个业务 Deployment 均 Ready；
  四项应用镜像与 2026-09-08 v0.1.34 发布记录一致，尚未包含后续首次使用能力。
- `/auth/session` 返回网页 HTML；正常的新版本启用登录后应返回 JSON
  `{"status":"anonymous"}`。HTTP 200 本身不能证明登录可用。
- `enterprise-doc-staging-4c4g` 是当前已核实的 SSH 入口，供应商为 Tencent Cloud。
  主机物理内存约 3.64 GiB、可用约 1.54 GiB；现有 Keycloak + PostgreSQL 预算为
  2.5 GiB，另需节点/主机余量，该原始配置不能同机部署。
- 原自托管身份 origin `https://auth.playlab.eu.cc` 未成功建立 TLS；当前改为优先托管认证。

用户随后明确业务主机固定为 4 核 4GB，不可扩容；另有空闲 2 核 3GB 翼龙容器云和
2 核 2GB 普通服务器，本轮只要求审核。保留业务 `single-node-4c4g` profile，身份服务可
改用托管认证；用户已指定优先尝试 Cloudflare Access，再尝试 Supabase Auth。
Keycloak 和 8GB 主机均不是业务必须条件。比较与待验范围见
[身份与资源审核](../../.trellis/tasks/09-21-saas-public-pilot-integration/identity-options-review.md)。
原 `infra/identity/hosting.md` 仅覆盖 K3s 同机模板，不能直接当作独立 2GB 节点部署方案。
若保留 Keycloak，仍需独立数据库、身份域名专用证书、私有管理和恢复验收；现有业务证书
不能覆盖 auth。身份镜像的现有发布限制仍有效，未在新增两台资源上执行任何部署。

2026-09-22 续作已在隔离本地依赖上启动记录中的四个候选镜像，实际通过迁移、checkpointer、
API/Worker/Web readiness、消费者队列连接、Web 匿名会话 JSON、准确 Code/S256 登录跳转和
正式运营命令预览；Chromium 登录页显示正常，未出现页面脚本错误。此验收没有请求真实
身份服务或模型，也未开通线上企业。远端 main 仍为 `3e0c015`，比本地少四个既有提交；
GitHub staging 尚未配置三个 `STAGING_BROWSER_AUTH_*` 变量。本次公网 HTTP 刷新连接被重置，
上文 v0.1.34 状态是上一轮现场结果。源码提交/推送清单已单独准备，尚未发布新镜像或部署。

## 接入本次新增的发布配置

管理员和 Deploy Staging 必须使用同一版源码、相同的正式应用镜像 digest，以及相同
的非秘密参数生成清单；旧 v0.1.34 镜像不能因加入以下配置就被当成支持新功能。

在 staging Environment 配置：

| 变量 | 本项目拟用值 |
| --- | --- |
| `STAGING_BROWSER_AUTH_ISSUER` | 所选托管服务实际返回的精确 issuer |
| `STAGING_BROWSER_AUTH_CLIENT_ID` | 为本产品注册的真实 confidential client ID |
| `STAGING_BROWSER_AUTH_OIDC_CONFIG` | 显式 authorization/token/JWKS 端点及所选非对称算法的 JSON，格式见下文 |

不配置 issuer 时，生成的浏览器认证为关闭状态；配置 issuer 后，生成器会绑定准确
Web origin、authorization/token/JWKS 地址和所选 RS256/ES256。显式 JSON 必须完整，端点
必须与 issuer 同源，不接受密码或其它字段。无需新增 workflow dispatch 输入。未配置 JSON
时仍兼容原 Keycloak realm 约定及默认 `docagent-web`/RS256，但本轮不以它为部署目标。
client secret 私下写入已有 `enterprise-doc-secrets` 的 `BROWSER_AUTH__CLIENT_SECRET`，
不放 ConfigMap、GitHub 普通变量、参数、报告或 Web 构建环境。

管理员原有 `configure_staging_manifest.py` 调用增加：

```powershell
$clientId = 'ed1c238b649b6fd566967970aedd187733d828463d32484cd7199112f8d5a92d'
$issuer = "https://billyciallo.cloudflareaccess.com/cdn-cgi/access/sso/oidc/$clientId"
$oidcConfig = @{
  authorization_endpoint = "$issuer/authorization"
  token_endpoint = "$issuer/token"
  jwks_uri = "$issuer/jwks"
  algorithms = @('RS256')
} | ConvertTo-Json -Compress
$browserArgs = @('--browser-auth-issuer', $issuer, '--browser-auth-client-id', $clientId,
  '--browser-auth-oidc-config', $oidcConfig)
# 将 @browserArgs 加入现有脚本调用；其余实际对象存储、模型、数据库出口与镜像参数照常绑定。
```

以上是已创建的 `DocAgent public pilot` SaaS/OIDC 应用参数，复用现有团队，拥有独立
策略并仅允许两个测试邮箱。应用 ID 为 `348ba6aa-60a0-4bd6-b9df-882b9ffaefe9`；
不要重复创建。client secret 已保存在本机受保护的凭据目录，尚未写入业务服务器。
授权入口已返回验证码页，两个测试邮箱各一次及 owner 另一次验证码均送达；测试程序的
回调捕获缺陷已在本地修复，真实换码及身份声明验收仍待完成，暂不启用产品登录。

所选服务的 confidential client 必须使用准确回调
`https://agent.playlab.eu.cc/auth/callback`、Code + S256、`openid email` 和真实 verified
邮箱声明，支持 `client_secret_basic`；不能通过批量标记邮箱已验证来替代投递验收。管理员凭据检查增加
`--require-browser-auth-client-secret`。该检查仍在原有私有通道内执行并只输出键名和结果，
不要为了运行检查把 Secret 明文导出到项目目录。

配置 hash 覆盖登录配置，原有业务进程在按清单发布时会重建。发布 runner 仍不读取
Secret，也不修改管理员拥有的 ConfigMap/Ingress/NetworkPolicy；先完成原有管理员
prerequisites 审批与应用，再运行现有 migration → workloads 顺序。

## 验证实际登录入口

Deploy Staging 在启用 issuer 后执行以下检查，与 bearer 上传烟测独立，不受
`run_smoke=false` 跳过。报告保存在原有 always 证据链中；失败、取消或未执行都不能
得到启用登录版本的 `passed` 发布记录。它不创建账户、不发信，也不调用模型。

```powershell
& .\.venv\Scripts\python.exe -B scripts/browser_identity_smoke.py `
  --web-origin https://agent.playlab.eu.cc `
  --issuer $issuer --client-id $clientId --oidc-config $oidcConfig `
  --output '<已存在证据目录中的全新文件路径>'
```

检查 HTTPS、匿名会话 JSON、准确 issuer/endpoints、Code/S256、所选算法和客户端认证方式；拒绝
重定向、HTML、禁用登录、错误端点及超大响应。结果不保存正文、cookie 或认证数据。
连接/读取有时限，workflow 另外限制该步骤为两分钟。此检查通过只证明匿名入口与
身份元数据可达，不能代替真实用户登录或完整业务验收。

Access 当前未提供标准 `code_challenge_methods_supported`。只有团队域名、精确客户端
issuer 路径及唯一 `authorization_code_with_pkce` grant 同时匹配时，检查才接受这个缺项，
并在回执注明未宣告具体 PKCE 方法；明确宣告不支持 S256 时仍失败。其
`token_endpoint_auth_methods` 字段别名也会检查 `client_secret_basic`。实际应用始终发出
S256，真实换码及 nonce、email_verified 等验证必须另行通过。

当前账户实测和可执行清单见
[托管认证接入尝试](../../.trellis/tasks/09-21-saas-public-pilot-integration/managed-identity-trial.md)。
尤其注意：Supabase 即使 OAuth server 未启用也会返回 discovery；需要验证实际授权端点，
不能仅凭元数据通过就宣布可以登录。原生 OTP 不依赖 OAuth server，但需单独产品适配。

## 完整试点仍需完成的操作

1. 发布包含首次使用能力的新业务镜像，按原流程完成实际数据库迁移；保存当前
   v0.1.34 的镜像与配置恢复依据。未验证时不能直接把旧版作为新客户的可兼容回滚。
2. 完成所选托管服务的真实验证码投递与身份验证。当前 `playarchive.eu.cc` 已验收的是
   收信；Access PIN 由 Access 发送，无需把收件箱改成发信服务。Supabase 则需核实项目
   邮件配置及收件人限制。真实试发使用获准地址，不能把收信验收当作登录成功。
3. 新版已提供私有 `python -m enterprise_doc_core.operations`，按
   [平台运营步骤](platform-operations.md) 为获准试用者签发准入，并查询接受后的 tenant_id
   配置有限周期。其本地真实数据库验证已完成，实际线上执行仍待新版及身份接入；旧本地
   CLI 保持 local/test 限制。首批试点在有效周期确认前保持生成关闭，避免接受准入与配置
   周期之间的 legacy 空档。不要通过修改 APP_ENV、seed 身份或开发 token 开户。
4. 获准模型调用后显式启用 `PRESALES__GENERATION_ENABLED`，成员邀请另启用
   `INVITATIONS__ENABLED`。本次接入登录不会自动开启二者。真实模型质量、花费和
   处理地区须记录，不能把受控模型的本地旅程当成公网业务通过。
5. 以已有公开资料演练包完成真实账号登录、上传、响应、引用核验/复核、CSV 导出。
   交付试点地址、登录方式、实际产出和已知限制，再邀请真实团队试用。

用户提出的默认演示账号采用独立“DocAgent 演示空间”和两个受控身份，owner 演示管理员，
member 演示普通成员。初始资料使用现有 Metabase 公开样例，按上述正式准入与权益步骤
开通；目前仅完成准备，尚未创建线上演示企业。细节见
[演示账号准备](../../.trellis/tasks/09-21-saas-public-pilot-integration/demo-account-plan.md)。

## 回滚

登录入口失败时先停止新试点准入，保留诊断结果。通过管理员配置将 issuer 撤回并重建
受影响进程，可关闭浏览器认证；这不是恢复旧 Web 正常使用的完整步骤，应用镜像也须
符合已验收的回滚方案。保留所有身份、准入、邀请和用量数据；不删表、不关闭验签或
TLS 校验，不把已有 cookie 身份静默转换成旧 bearer token。
