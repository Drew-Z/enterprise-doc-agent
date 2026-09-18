# 发布与公网验收研究记录

依据为 source-index.json 中保留的当前官方正文和本任务实际 API/Chromium 输出。所有 Cloudflare 服务请求限定在选定账号/zone；报告不包含 API Token、站点/管理员/签名密钥或 Address JWT。

## 固定输入与地区

旧包 manifest SHA-256 为 `48ce026cc9360c5644b8b36c4c42fa6b5f72648e39daf96e7d8c176b5cb63f21`，Worker JS SHA-256 为 `dfbd83c94768744e4cd79d296c6d922680541440d52ac1e8cd6eeb3fe6a5cdf3`。部署校验完整文件集、每个大小/SHA 并持有内存字节，禁止验证后再从可变目录重读。未重新构建或修改固定包。

D1 create 接受 `primary_location_hint:apac` 和 `read_replication.mode:disabled`。实际数据库 jurisdiction 为 null，查询报告 APAC/SIN/primary。hint 与 jurisdiction、邮件处理地区、身份托管地区是不同事实。当前主库无正文；表结构和初始设置只初始化一次。

## 四份失败记录

| 报告 | 最后步骤/结果 | 后续处理 |
| --- | --- | --- |
| deployment-state.json | 第一个 assets bucket 传输失败，HTTP 未知 | 核验专用库/零数据后显式恢复 |
| deployment-resume-01.json | 第二个 bucket ReadTimeout | 改为逐文件内容寻址上传 |
| deployment-resume-02.json | 第三个小文件 RemoteProtocolError | 排除“只是大包慢”的充分解释；没有认定代理/VPN为根因 |
| deployment-resume-03.json | 剩余 16 个文件、Worker、三个秘密均上传成功；配置验证错误停止 | 不再运行资产恢复；改为已有 Worker 发布 |

客户端最终使用 `Connection: close` 后一次完成剩余 assets。它与成功相关，但没有证明连接复用就是唯一根因。每次恢复写新报告，旧失败文件未回填成成功；无自动创建重试或资源删除。

## API 回读错误及修正

上传接受 `assets.config.run_worker_first=true`，但不能从旧 `/settings` 响应推定相同返回形态。官方 **Get Script Settings** 的实际路径是 `/script-settings`，只用于脚本级日志等设置。

正确的运行版本读取：

```text
GET /accounts/{account}/workers/scripts/{name}/deployments
  result.deployments[0] 是当前服务流量的部署
  必须只有一个 versions[]，percentage=100
GET /accounts/{account}/workers/scripts/{name}/versions/{version_id}
  resources.bindings
  resources.script.handlers / etag
  resources.script_runtime.compatibility_date / compatibility_flags
  resources.script_runtime.assets.raw_run_worker_first=true
  resources.script_runtime.assets.serve_directly=false
```

`raw_run_worker_first` 和 `serve_directly` 的形态来自实际版本回读；它们不是原始上传配置字段。HTTP fake 已改为正式端点和实际资源结构。部署列表的第一项语义由 list 文档确认；get-one 文档不能替代列表规则。

当前版本 `a7b3cb12-0210-4050-bfb6-a4acb69d679c`、deployment `88f4b6ef-1f81-45b7-8e39-37355e05189e` 在发布前后相同；etag 为 `3271be04ec968e4d884cea1cf15e1ba72aaf39edd2ef140038c99b328e78fd07`。三个 secret PUT 各产生一个新版本属于正常 API 行为，不再重复写入。

## 日志的空值

当前 Workers Logs 文档同时说明新 Worker 的工具默认启用设置，以及写入日志需要添加 observability 配置。因此，本次 `/script-settings` 的 `observability:null` 不能独立证明关闭或启用。

发布前使用正式 PATCH `/script-settings`，明确请求 `enabled=false`、logs/traces 的 enabled/persist=false、采样率 0、空 destinations、logpush=false、空 tail_consumers。请求成功后再 GET；服务继续返回 null。验收保留“关闭请求已确认”和“原始回读为空”两个字段，而没有伪造 `enabled:false` 的读结果。检查器在没有显式关闭确认时会拒绝 null，若仍返回 enabled=true 或存在导出则停止发布。

## 自动 Web Analytics 注入

第一次公网 API/网页登录成功，但浏览器尝试加载 `https://static.cloudflareinsights.com/beacon.min.js/...`。诊断浏览器在未输入凭据时也观察到注入。普通 Python 请求的 HTML 与固定包字节完全相同；因此不能只用 curl/HTTP 哈希验证浏览器无外部资源。

账号已有 6 个 Web Analytics 站点，选定 zone 的规则集 `ca50d3f0-8d97-417d-91f5-61d68932b9ef` 自动启用，并有一条既有 `host=* / paths=* / inclusive=true` 规则。`lite=true` 在当前 API 文档中表示不向 EU 访客注入，不是“已关闭统计”或规则额度的原因证明。

先尝试只新增 inbox 主机排除规则，收到 409/code10012。回读确认规则未变后，只显式复现一次该不含秘密的请求以捕获诊断，错误为 `web_analytics.configuration.api.maxRulesError`；没有自动循环重试，也不推断所有套餐的统一规则上限。

该 zone 的 DNS 只有本轮邮箱入口。随后先在集中恢复组保存其原设置，再用已记录 site ID 进行 `PUT /rum/site_info/{id}`，保持 auto_install=true/lite=true/zone_tag，设置 enabled=false。auto_install=true 是 enabled 选项的 API 前提；有效开关是回读的 ruleset.enabled=false。其他 5 个站点和既有规则逐项不变，没有购买或升级套餐。

public-02 复用两个现有邮箱，21 项通过，0 外部请求、0 page errors，浏览器关闭后才写 passed。首次失败报告和三张截图保留。

## Routing 与 Sending

官方子域指南使用 Dashboard：Compute → Email Service → Email Routing → apex → Settings → Subdomains。旧 DNS 查询的 subdomain 参数已弃用，根域 enable 的 name 也不能猜作子域创建接口。网页 custom domain 完成后，Routing 状态仍 disabled/unconfigured、规则未变、邮箱子域无 MX。

Email Sending 是每个域/子域独立开通。当前没有 Sending、付费升级、真实发信或真实收信结果。两个已验证的账号目的地仍不作为获准试发名单。Keycloak 本地 capture 和产品本地 S5 验收保持原边界。

## 可复核的研究入口

研究命令使用 `smart-search fetch '<source-index 中的官方 URL>' --format json --output <任务临时路径>`，provider 与原始返回的 SHA 都已保留。没有修改搜索配置、安装依赖、运行 Wrangler 部署、重新构建旧包或把网页验收升级成完整 SaaS 商业发布结论。
