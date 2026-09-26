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

准入接受与周期配置仍是两个事务。本分支2026-09-24改动在`APP_ENV=staging/production`
的API和后台Worker中强制有效且有限的周期：无周期、过期/未开始或旧无限额配置均拒绝
新售前生成，用量页显示不可用及剩余零。有限周期额度为零返回额度不足。local/test
保留legacy兼容，已有持久预约仍按原周期结算或释放，查看、复核和导出不受影响。

此改动**尚未部署**；v0.1.44仍需原有手动控制。切换前逐一查询现有正式企业、补齐有限
周期并确认，停止接收新生成且排空旧无账本在途请求，再发布一致的API/Worker版本。
不要删除旧周期、重置使用量或把APP_ENV改成local来绕过约束。新企业可先正常准入，
运营确认周期后才开放生成；这不是自助支付或原子套餐开通。本分支另增加 Agent 任务、
文档处理配额和逐次供应商调用记录，以下配置需要包含新迁移的候选版本。

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
  '--period-end', $pilotPeriodStart.AddDays(7).ToString('o'), '--request-limit', '100',
  '--agent-task-limit', '20', '--document-bytes-limit', '104857600'
)
& $taskPython -B -m enterprise_doc_core.operations @operatorTarget @entitlementRequest
& $taskPython -B -m enterprise_doc_core.operations @operatorTarget @entitlementRequest --execute
& $taskPython -B -m enterprise_doc_core.operations @operatorTarget entitlement show `
  --tenant-id $pilotTenantId --entitlement-id $pilotEntitlementId
```

示例数字用于说明命令，不是已批准的客户套餐。周期不能重叠，配置只追加。limit 必须是
有限非负整数，0 表示拒绝新的生成。相同 ID 和相同配置重放返回 replayed=true，保持已用
额度和唯一配置审计。改变配置、版本过期或租户不符会拒绝；不要换一个新 ID 掩盖失败。
`--request-limit` 保留售前成功生成口径；`--agent-task-limit` 按成功 Agent 任务计数；
`--document-bytes-limit` 按成功处理的原文件字节数累计，独立于存储占用。后两项省略时
为 **0**，不代表免费或无限额度。等待审批保留任务预约，失败、拒绝或取消释放，成功
只结算一次；审批跨周期仍结算原周期。文档自动重试共用预约，人工重开失败处理生成
新的处理预约，已成功结果重放不重复扣量。这些额度不代替供应商费用和支付账单。

### 给既有周期补充产品额度

迁移不替现有企业自动选择套餐。先用 `entitlement show` 核对周期 ID、该周期的 `version`
及已有计数，再补缺失的两项配额。此命令的 `--expected-version` 是**目标周期自身版本**，
不同于新建周期时的 latestVersion。命令不增加版本、不改变售前额度、不清空任何用量。

```powershell
$productRequest = @(
  'entitlement', 'configure-products', '--tenant-id', $pilotTenantId,
  '--entitlement-id', $pilotEntitlementId, '--expected-version', '<该周期 version>',
  '--agent-task-limit', '20', '--document-bytes-limit', '104857600'
)
& $taskPython -B -m enterprise_doc_core.operations @operatorTarget @productRequest
& $taskPython -B -m enterprise_doc_core.operations @operatorTarget @productRequest --execute
```

只补尚未存在的配额；相同请求重放幂等，已有上限不同则拒绝，不支持借此重置额度或
在线改套餐。新建的已结束周期配额被拒绝。两个本地/正式 CLI 均默认预览，`--execute`
才写入。服务端会拒绝正式环境中没有当前周期或产品配额的新 Agent/文档处理。

### 调用上限与费用观察

API、Worker、Consumer 和其 MCP 子进程使用同一组 `PROVIDER_USAGE__*` 环境配置：

| 环境变量 | 默认 | 作用范围 |
| --- | --- | --- |
| `PROVIDER_USAGE__DAILY_CALL_LIMIT` | 1000 | 每个企业的 UTC 自然日内 Agent 和向量 HTTP 尝试总数 |
| `PROVIDER_USAGE__AGENT_CALL_LIMIT` | 12 | 同一个 Agent run，包含修复、主备及恢复重试 |
| `PROVIDER_USAGE__DOCUMENT_CALL_LIMIT` | 2048 | 同一次文档处理，包含向量拆批及重试 |
| `PROVIDER_USAGE__QUERY_CALL_LIMIT` | 6 | 同一次工具检索，或同一售前尝试对同一来源的检索 |

这些是保守的有限次数默认值，尚无真实容量/人民币成本校准，不能称为全站金额预算。
售前 Chat 沿用 `presales_provider_calls` 和原有售前预算，不包含在新调用汇总中；
独立运维 embedding 探针不属于客户业务账本。任何入口缺少正式计量作用域时拒绝派发。

每次 HTTP 前先提交 `provider_dispatches`，然后在事务外请求供应商。HTTP 2xx 只表示
收到了响应，不表示格式、引用或业务成功。超时、取消、传输失败和进程中断可能已经
产生费用；`dispatched` 遗留记录保留为不确定。未配置版本化价目表时金额和币种始终
为空。用量页展示当前周期的尝试、响应不确定数、费用未知数及已报告 Token，不生成
账单。对账应在私有运维环境导出双方记录，保留未知项；真实主备渠道对账尚未完成。

### 导出企业对账明细（候选）

包含 `0031` 迁移的版本提供私有只读导出，使用前面的 `$operatorTarget` 核对目标：

```powershell
& $taskPython -B -m enterprise_doc_core.operations @operatorTarget usage export `
  --tenant-id $pilotTenantId `
  --start '2026-09-01T00:00:00+08:00' --end '2026-10-01T00:00:00+08:00' `
  --limit-per-source 5000
```

命令直接查询并输出 JSON，不需要 `--execute`。保存结果时使用已有私有目录，保留
目标、操作者、时间窗和导出时间；不要上传客户明细到 GitHub 或普通 CI 产物。
收集不到数据库结果时返回失败，不输出看似成功的空账单或部分明细。

- `provider_calls` 分别来自 Agent/向量和售前调用表，保留主备、错误、超时、取消和
  未确认记录，以及供应商返回的有效请求/响应 ID。旧行没有 ID 时仍为空，不伪造补齐。
- `business_events` 是成功扣量或释放的事件；任务、字节和售前次数分开，保留原周期 ID。
  例如主路超时、备用成功是两次供应商调用，但只消耗一次成功业务额度。
- 调用按开始时间，业务事件按发生时间，均包含 start、不包含 end。范围最多 31 天；
  五个来源各最多 5000 条。超限返回 `reconciliation_export_limit_exceeded`，请缩小
  时间窗，不把截断的结果用于对账。整次查询最多 30 秒，数据库使用一致的只读快照。
- `legacy_presales_attempts` 单列旧同步售前的汇总及未知结果，按创建时间筛选。
  它们缺少逐次调用明细，不参与供应商调用汇总；管理探针和计量上线前的缺失记录也
  不在覆盖范围内。`not_sent` 可见但不计入可能产生费用的调用。
- `estimated_cost/currency` 未知时为空，汇总列出未知费用/Token 数量；有金额也不跨
  币种相加。导出反映查询当时的记录状态，稍后重导可出现新确认结果，不是历史截点账单。

该功能用于与供应商账单人工核对，不自动生成人民币应付金额，也不证明已完成真实
渠道对账。先对齐渠道、模型、时区和请求 ID，再逐项核对失败/未知调用；无法匹配的
保留为待核查。版本化价目表、供应商账单和正式商业验收仍待完成。

### 候选迁移与回滚顺序

1. 核对目标及已部署 SHA，准备经验证的 DB/对象恢复点，暂停新任务入口并排空旧任务。
   包括待审批的旧 Agent；不能让旧无预约 run 混入新的正式 Worker。
2. 应用新迁移 `20260924_0029`（产品额度、处理归属）、`20260924_0030`（调用记录）
   及 `20260924_0031`（供应商标识与企业时间索引）。
   旧迁移不改写；为现有企业补配额，核对预览、执行回执和 `show`。
3. 旧演示企业同样没有产品配额。可等待其正常过期并完成既有清理，或逐个核对后补充
   与演示上限一致的配额；不能重置其历史用量。新演示企业固定 Agent=0（原演示权限
   本就不允许 Agent）、文档处理=10 MiB，既有演示生成/容量限制继续生效。
4. 一致升级 API、Worker、Consumer/MCP 和 Web 后，验证 owner 用量、实际任务成功、
   取消与失败释放，再恢复入口。严格 Web 契约要求新 API 的两个新增字段。
5. 有产品预约、用量、调用历史或新增供应商标识时，对应 downgrade 主动拒绝。回滚优先暂停写入并前向修复，
   保留账本；不能删除历史以强制降级，也不能直接退回会绕过计量的旧 Worker。必要时
   使用预先核验的整套恢复点，并明确恢复点后的业务数据差额。

当前仅完成隔离 PostgreSQL 和受控 HTTP/浏览器验证，尚未迁移正式数据库或部署。

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
