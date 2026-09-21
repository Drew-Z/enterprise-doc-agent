# 平台运营：私有企业准入与周期额度

`python -m enterprise_doc_core.operations` 为获准试用者签发企业准入，并为已开通企业
配置有限的周期额度。代码随 Core wheel 进入业务镜像；不依赖仓库的 `scripts/` 目录。
本次已做本地真实数据库验收，线上执行仍需新版本、真实身份登录及已获准的运营参数。

## 运行位置与权限

命令从已有管理员权限的私有运维环境运行，授权依托 SSH/Kubernetes 管理通道和数据库
凭据。`--operator` 是审计归因，填写 owner、admin 或其他字符串不会授予权限。没有
公网运营接口，不接受普通租户 token/cookie，不给发布 runner 新增 Secret 或 exec 权限。

运行环境须安装相同发布版本的 Core 包，使用已有私有配置提供数据库连接。也可以在
经过审查的短命管理容器中运行，并挂载私有持久目录存放准入凭证。现有 API Pod 使用
只读根文件系统，没有凭证输出卷；不要为运行命令关闭它的只读配置或把开通码写进日志。
若从本地管理环境通过已授权隧道连接实际 staging 数据库，APP_ENV 仍为 staging。

签发使用的凭证目录必须是已存在的绝对路径。文件采用独占创建、POSIX 0600 或 Windows
owner-only DACL，写入并同步后才尝试数据库事务。保存在仓库和同步目录之外，不上传
GitHub/CI 产物。密码及数据库 URL 不放命令参数、截图或普通操作回执。

## 必要配置与目标核对

命令只加载当前进程环境，**不会读取工作目录 `.env`**，也没有默认数据库 URL。

| 配置 | 要求 |
| --- | --- |
| `APP_ENV` | 明确的 `staging` 或 `production`，与实际目标一致 |
| `DATABASE__URL` | 管理员私下提供的 `postgresql+psycopg://...` 连接，明确主机及数据库名 |
| `BROWSER_AUTH__ENABLED` | 签发准入时须为 true |
| `BROWSER_AUTH__ISSUER` | 签发绑定实际应用启用的准确 HTTPS issuer，无 userinfo/query/fragment |

查询、撤销与额度维护不要求身份服务当前开启，便于停用登录时继续处理已有记录。
数据库连接可继承 `DATABASE__CONNECT_TIMEOUT_SECONDS` 等原 DatabaseSettings 有界参数。
禁止连接串中的 host/hostaddr/port/dbname/service/servicefile 覆盖以及环境 PGHOSTADDR、
PGPORT、PGSERVICE、PGSERVICEFILE，防止实际连接偏离审阅目标。

所有操作要求 `--environment`、`--database-host`、`--database-name`；非 5432 端口还须
`--database-port`。这四项必须与配置逐一吻合，包括隧道的本地端口。它们是误操作检查，
不是数据库权限或远端部署身份的证明。操作者仍须从正式配置确认目标和相同版本的迁移。

下面是 PowerShell 参数示例；替换目标与账户标签，数据库密码由既有私有配置载入：

```powershell
$taskPython = '.\.venv\Scripts\python.exe'
$operatorTarget = @(
  '--environment', 'staging',
  '--database-host', '<实际连接主机>',
  '--database-port', '5432',
  '--database-name', '<实际数据库名>',
  '--operator', '<已获管理权限的账户标识>',
  '--reason', '<获准试点或工单编号>'
)
```

## 1. 签发准入，核对后交付

只为已批准的准确邮箱签发；身份 issuer 来自应用配置，不能通过命令参数替换。
容量和席位为初始资源限制，不代表收费订单。到期时间必须带时区，距执行时间不超过 30 天。

```powershell
$admissionFile = '<私有目录中的全新绝对 JSON 文件路径>'
$admissionExpiry = (Get-Date).ToUniversalTime().AddHours(24).ToString('o')
$admissionRequest = @(
  'admission', 'issue', '--email', '<获准的准确邮箱>',
  '--expires-at', $admissionExpiry,
  '--quota-bytes', '104857600', '--seat-limit', '2',
  '--credential-file', $admissionFile
)
& $taskPython -B -m enterprise_doc_core.operations @operatorTarget @admissionRequest
# 核对预览后，使用同一组参数实际执行：
& $taskPython -B -m enterprise_doc_core.operations @operatorTarget @admissionRequest --execute
```

默认 `status=preview, databaseValidated=false`，没有连接数据库或创建文件。只有
`status=confirmed` 才确认签发。凭证文件始终标记 prepared，文件存在不能证明数据库成功。

通过已获准的私有交付方式把开通码交给对应收件人。收件人正常登录并验证邮箱，在产品的
“开通新企业”输入开通码和企业名称；也可私下使用 `/#/admission?token=<开通码>` 链接。
若尚未登录，先登录再重新打开原链接。不要把 token 放在普通 URL query 或共享日志中。
本命令不会发信，也不会伪造 email_verified、创建浏览器身份或自动替客户接受准入。

保存回执里的 grantId，再查询实际接受状态及 tenant_id：

```powershell
& $taskPython -B -m enterprise_doc_core.operations @operatorTarget admission show `
  --grant-id '<签发回执中的 grantId>'
```

## 2. 配置试点周期与有限额度

准入接受与周期配置是两个事务。首批试点保持生成关闭，直到按接受回执中的 tenant_id
配置并确认有效周期，再按发布流程显式启用 `PRESALES__GENERATION_ENABLED`。没有周期的
企业仍沿用 legacy 行为；不能把这一手动两步流程当成持续自助订阅或原子套餐开通。

先查询该企业，使用 latestVersion 作为 expected-version；首次配置为 0：

```powershell
$pilotTenantId = '<准入 show 返回的 tenant_id>'
& $taskPython -B -m enterprise_doc_core.operations @operatorTarget entitlement list `
  --tenant-id $pilotTenantId --limit 20

# 只在建立一项新配置时生成 ID，记录这组参数供查询和原样重试。
$pilotEntitlementId = [guid]::NewGuid().ToString()
$pilotPeriodStart = (Get-Date).ToUniversalTime()
$entitlementRequest = @(
  'entitlement', 'configure', '--tenant-id', $pilotTenantId,
  '--entitlement-id', $pilotEntitlementId, '--expected-version', '0',
  '--plan-code', 'pilot', '--period-start', $pilotPeriodStart.ToString('o'),
  '--period-end', $pilotPeriodStart.AddDays(7).ToString('o'), '--request-limit', '100'
)
& $taskPython -B -m enterprise_doc_core.operations @operatorTarget @entitlementRequest
& $taskPython -B -m enterprise_doc_core.operations @operatorTarget @entitlementRequest --execute
& $taskPython -B -m enterprise_doc_core.operations @operatorTarget entitlement show `
  --tenant-id $pilotTenantId --entitlement-id $pilotEntitlementId
```

示例数字用于说明命令，不是已批准的客户套餐。周期不能重叠，配置只追加。limit 必须是
有限非负整数，0 表示拒绝新的生成。相同 ID 和相同配置重放返回 replayed=true，保持已用
额度和唯一配置审计。改变配置、版本过期或租户不符会拒绝；不要换一个新 ID 掩盖失败。
该额度以 provider_request 计数，不代替真实模型费用、支付记录或最终商业计量规则。

## 3. 撤销未使用准入与恢复

```powershell
$revokeRequest = @('admission', 'revoke', '--grant-id', '<待撤销 grantId>')
& $taskPython -B -m enterprise_doc_core.operations @operatorTarget @revokeRequest
& $taskPython -B -m enterprise_doc_core.operations @operatorTarget @revokeRequest --execute
```

已接受的准入不能用 revoke 删除企业或成员；后续成员治理沿用产品原有权限流程。

| 结果 | 处理 |
| --- | --- |
| exit 0 / preview | 仅输入和目标校验；不代表数据库状态、重叠或版本已核对 |
| exit 0 / confirmed | 数据库操作或查询已确认，保留 ID 和不含凭证的回执 |
| exit 1 / file_failed | 数据库写入未尝试；保留并检查目标文件，不覆盖已有凭证 |
| exit 1 / not_confirmed | 写入结果未确认；保留文件/原参数，用同一 ID 的 show 核查，不自动重试签发 |
| exit 1 / failed | 只读查询失败，检查可达性后重查，不据此判断记录不存在 |
| exit 2 / failed | 参数、运行配置或目标不符；修正后重新预览 |

数据库操作总时限 30 秒，领域锁仍使用既有有界等待。错误输出只包含稳定 code。
签发超时/断连后即使文件存在也先 show；若为 pending，继续使用原文件；如果明确查无记录，
先核清失败回执再新发。周期配置未确认时，先 show 原 tenantId/entitlementId，再原样重放。

回退命令版本不删除准入、企业、周期或账本。旧本地命令仍只允许 local/test + loopback；
不要通过改 APP_ENV、seed 身份或删除周期来绕过运营入口。
