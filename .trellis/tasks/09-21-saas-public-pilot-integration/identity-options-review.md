# 身份服务与现有资源审核

## 结论与本轮边界

用户明确现有 4 核 4GB 业务服务器不能扩容；另有全新空闲的 2 核 3GB 翼龙面板容器云、
2 核 2GB 国内服务器。本轮用户只要求审核判断，不要求连接、实测或部署这两台资源。
不再把购买 8GB 主机、扩容或部署 Keycloak 到业务节点列为必要前置条件。

Keycloak 是 2026-09-16 为复用现有 OIDC 客户端而作出的实施选型，不是用户指定的产品，
也不是业务功能的必需组件。原选型依据见
`../09-16-saas-native-mail-identity/identity-decision.md:3-9`。

用户随后明确选择优先尝试 **Cloudflare Access 邮箱 PIN/OIDC、Supabase Auth 邮箱验证码**。
当前按此顺序推进托管认证。下述两台新资源只保留适用性审核；不继续自托管身份部署。
用户已选现有团队内独立应用和策略，Access 客户端已创建并回读核验，真实验证码登录仍待
完成。实际账户状态、接入参数及缺项见 `managed-identity-trial.md`。

## 三处资源的适用性

| 资源 | 建议用途 | 判断边界 |
| --- | --- | --- |
| 现有 4 核 4GB 服务器 | 保留文档处理、API、Worker、Web 等业务服务 | 2026-09-21 只读实测物理内存约 3.64 GiB，可用约 1.54 GiB；不再叠加原 2.5 GiB 身份预算 |
| 全新 2 核 2GB 普通服务器 | 独立身份节点的优先验证对象 | 以能完整控制系统与服务为前提；需要小内存配置与真实启动/登录/恢复测试，不能直接照搬 Keycloak 2 GiB + PostgreSQL 512 MiB 的旧模板 |
| 全新 2 核 3GB 翼龙容器云 | 内存更宽裕的备选身份运行环境 | 面板通常管理单个应用容器，不能假定可在其中启动 Docker Compose；取决于镜像/启动命令、持久化目录、端口、数据库连通和重启策略。仅凭 3GB 规格不能判定部署条件已经齐全 |

2GB 普通服务器的试验预算可以从 Keycloak 容器上限 1 GiB、数据库 256 MiB、系统与运维
预留至少 512 MiB 开始核验。此数字是**待测试配置**，不是实测占用或上线承诺；JVM 堆不能
等同于容器上限，还需给 metaspace、线程、直接内存与临时文件留空间。若测试不通过，
不得仅修改 requests 或删掉余量检查来宣布可用。无需为了分摊两个进程就默认启用两台新机器。

## CF 邮箱与认证不是同一项能力

已部署的 `cloudflare_temp_email` 私有站点负责收件箱和邮件访问，现有公开验收只证明收信
及两个合成邮箱的隔离，发信仍关闭。其站点密码、管理员密码和 Address JWT 保护的是邮箱，
不提供本项目需要的 OIDC 授权码兑换、ID token、可信 issuer/subject 或产品企业会话。
不能把“可以打开收件箱”直接当作“可以访问某家企业资料”。

邮箱仍然有价值：它可以接收验证邮件、验证码和找回账号邮件。产品身份服务负责生成并
验证这些一次性凭据，产品现有准入、成员绑定和会话服务负责业务权限。

普通 Cloudflare Worker 也不是可直接运行现有 Keycloak JVM 镜像的环境。若想把认证放到
Cloudflare，需要使用 Access 等独立认证产品，或实现独立的 Workers 认证服务；这与复用
现有收件箱是不同的变更。

## 无需自托管 Keycloak 的替代路线

| 路线 | 能提供什么 | 本项目尚需核实/改动 |
| --- | --- | --- |
| Cloudflare Access + 邮箱一次性 PIN，结合 Access for SaaS OIDC | 项目专用 SaaS/OIDC 应用及策略已建成，两个测试邮箱各一次真实 PIN 已送达；不占业务主机内存 | 首次提交后未观察到产品回调；`email_verified`、`nonce`、稳定 `sub`、验签及换码尚未验证；套餐查询仍 403 |
| 托管 Supabase Auth | 官方支持邮件 OTP、Magic Link 及会话；账户中有一个 ACTIVE_HEALTHY 项目 | 尚未证明该项目可直接复用现有业务认证；原生 OTP API 不是现有 OIDC 回调接口，需要适配。默认邮件只面向项目团队且限额很低，真实客户登录还需正式发信方案 |
| 在 CF Workers 自建/集成认证库 | 可把验证码、账号和会话逻辑放到边缘侧 | 需要维护一次性消费、限速、恢复、签名/密钥、会话和审计；现有邮箱不会自动补齐这些能力，不建议为了省一个身份容器就仓促自建完整 IdP |

Cloudflare Access 的官方 OIDC 文档说标准 claims 在“if available”时转发，并未保证所有
登录方式都返回本项目要求的字段。搜索模型关于“OTP 必定缺失或为 false 的 email_verified”
的断言没有得到所抓取官方原文支持，**不采用该断言作为排除理由**，保留实际兼容性验证。

现有 `OidcClient` 本身使用显式 issuer、authorization/token/JWKS endpoints，具备更换标准
OIDC 服务的基础。发布生成器和 smoke 已支持显式托管端点与算法，同时保留原 Keycloak
约定；本轮还兼容了 Access 的元数据格式差异。没有削弱应用验签、nonce/email_verified
校验或自动按邮箱合并账号。

## 已核实的资料与事实

- [Cloudflare Access 一次性 PIN](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/)
- [Cloudflare 通用 OIDC SaaS 应用](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/saas-apps/generic-oidc-saas/)
- [Supabase 邮件无密码登录](https://supabase.com/docs/guides/auth/auth-email-passwordless)
- [Supabase 正式邮件发送要求](https://supabase.com/docs/guides/auth/auth-smtp)
- 本项目邮箱事实：`infra/cloudflare_mail/README.md:31-53`。
- 产品身份验证边界：`apps/api/src/enterprise_doc_api/browser_auth/oidc.py:134-207`。
- 当前业务主机新观察：`fixed-4g-host-observation.json`、`fixed-4g-workload-observation.json`。

官方页面以 `smart-search fetch <URL> --format json` 抓取，原文分别保存为本目录的
`identity-cf-access-otp.json`、`identity-cf-access-oidc.json`、
`identity-supabase-passwordless.json`、`identity-supabase-mail.json`。
最初资源审核没有发送邮件、创建认证账号、更改云端设置或连接新增两台服务器。用户后续
明确选择后，已在 Cloudflare 创建独立 Access 应用/策略，并按单独授权发出两封测试验证码；
仍未修改 Supabase 或连接新机。

## 已完成的独立发布准备

确认 GitHub main 为 `3e0c015a10992006d0bc531c8928e68c4284d7f6`，本地 HEAD 为
`b6d14203e3a3eafe733986a88086bf68ba5395f8`，本地有 4 个尚未推送的提交。
候选输入为 HEAD 中 496 个构建文件，加上其中新增的 3 个已验证运营模块文件；准确清单
与哈希见 `release-candidate-source.json`。没有把其余 1,908 项既有 WIP 整体加入构建或提交。

三个后端镜像构建命令成功。Web 先遇 npm 下载错误、再遇 Alpine 下载/解包错误；最后一次
客户端达到 300 秒时限，之后检查发现镜像与 iid 文件已经生成且一致。该观察单独记录，
不改写原超时为成功；容器运行检查、远端构建/签名和公网部署仍未完成。
这批本地候选不等于正式发布，原有身份镜像发布限制也未因资源变化被解除。
