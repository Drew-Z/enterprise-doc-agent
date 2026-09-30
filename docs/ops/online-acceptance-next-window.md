# 当前部署、回退与验收边界

2026-09-29 UTC：当前部署为 **v0.1.45-rc.8 / dab8480b3a5cb37a849d31f74834db6e1ef23ac5**，数据库 `20260924_0031`。统一用户入口见[最终验收](final-project-acceptance.md)，本轮实测见[机器记录](../../.trellis/tasks/09-24-commercial-operations-acceptance/rc8-release-validation.json)。

## 发布与回退

[发布流水线 36638635088](https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/36638635088)四镜像及清单成功；清单 SHA-256 为 `019e67906a1d7e57e4acc9fb65d4ef577ffac61a0c89d5b4e0adedbf7c46e76d`。56 份文件哈希、镜像及证明主题复核通过；密码学签名验证由该 CI 执行。

以下位于 `ghcr.io/drew-z/enterprise-doc-<组件>`，使用完整 `image@sha256`：

| 组件 | 当前 rc.8 | 回退 rc.7 |
| --- | --- | --- |
| api | `sha256:3a740771f631848798dfaa3e82a1c28b68ddf8bed8a203c2fe7a3a2c202ad021` | `sha256:1bd29a0ab8ec4427a19099e8f72245c4a7570c23145350b1a0db070d927a7c7e` |
| worker | `sha256:26f02e5f1af022cb1b702ea8f9e679327e917c50fb161c5ac6d5de43d959d23b` | `sha256:1df49907734385623bbce20c6a8901795a2002bff1705c76b14814afa962fa04` |
| consumer | `sha256:15f973b3d37fc6391583fff1b24bad9095c5437062f50ec10a4ccebd299d9e9b` | `sha256:c62be34aa8cfe5a44e1fd15fc3073829d9bd8062112f889f7f5769952bdb48d9` |
| web | `sha256:a1a6dbe4c0d27c9f3720e64af9220e26d933018a09f84deb151a1a220952e0bd` | `sha256:101b0019f2f1da6924bcbe0a546db5176f1726a7aa0e083a4928ce9fd54fd7ad` |

独立守卫在 91.292 秒内完成镜像切换。API、Worker、Consumer、Web 和 Redis 均就绪；配置、凭据和 0031 不变。四个 GitHub staging 回退变量已读回，其他变量未改。远端临时运行目录与 11 个文件已清理，原恢复输入集中本地保存。当前回退引用存在不等于本窗口执行过 rc.8→rc.7 故障演练；数据库不得降回 0027。

## 实际业务配置

- 主路 `grok-4.7`，备用 `grok-4.6`；三后端进程一致，两个模型名不证明供应商独立。
- 售前 `primary`、后台和自动切换启用、并发 1。每行执行上限 150 秒，排队上限 900 秒；首次模型为备用路预留时间。
- 原 pilot 于北京时间 2026-09-29 18:57:59 到期，不自动延期或重置。本轮另建四任务、30 分钟的有限合成周期，仅用于诊断。

rc.8另建两企业各80任务的有限容量周期，总窗口7200秒、模型320/向量4320上限，保留既有每企业向量日限1000。计划在第三个失败后停交；两企业均不用于延长原pilot。

## 已执行与仍待完成

原 Agent 的真实上传、检索/MCP、生成、产物和一次计量，以及同键重放已通过。原售前后台离线完成、技术复核、CSV 和账本证据继续有效。rc.4 再次核验 Agent 结果/引用/下载/刷新；静态字节来自运行中的 Web 镜像，业务 API 真实走公网，认证使用已授权 staging 自动化适配。该阶段公网静态文件和售前 CSV 在本机代理上超时，失败保留。随后 rc.5 直接使用公网静态文件与真实 API 的 CDP 验证 Agent 结果/引用/下载/刷新、售前 CSV/离线后重开全部通过，无新业务提交；rc.6 的 HTML/JS/CSS 哈希与受测版本完全相同。

rc.4新四任务的上传、解析、检索、生成、重放、复核、CSV和一次结算全通过，模型4、向量12次。受理中位数2,158.8ms、p95 5,663.7ms，2秒未通过；旧160任务9/4/147保持不变。

rc.8容量最终为30通过/5失败/125未执行：第三个失败后另两个在途任务收尾失败，不能缩小分母。32个受理样本p50 3,535.434ms、p95 7,474.234ms；上传、解析、检索目标亦未全部满足。实际34模型/100向量，后查32生成中31成功/1失败，后台后来成功不改判采样超时。两次真实主路错误经备用路恢复，各只消费一次。61次遥测均收回，但队列来源出现失败/新鲜度缺口，观察状态仍不完整。

两项 Windows 无窗口运维任务继续运行，备份实际还原和对象验证、有限快照轮换保持。个人电脑停机后的连续运行、独立业务/发布审核、值班及整机 RPO/RTO 仍需要实际完成；不配置常驻备用机不豁免按需恢复验证。

## 对账与历史记录

应用侧调用/业务账本保留实际调用与一次结算；未知费用保持null。Windhub最新84条账单合计486,289配额，与令牌累计一致，仍为CUSTOM/500000配额每单位。rc.8已发布x-oneapi-request-id优先识别，但34模型记录仅2个ID精确匹配，且均为type=5、quota=0错误记录，成功调用仍未对账通过。账单未返回可用upstream_request_id，不以时间/token相近填补关联。主备地址和密钥不同，主路账单不能当作备用账单；向量旧余额端点退役，实际币种/金额仍需核实。

旧 [Agent 验收](online-business-acceptance.md)、[售前验收](online-presales-browser-acceptance.md)、[rc.3 发布记录](../../.trellis/tasks/09-24-commercial-operations-acceptance/final-delivery-validation.json)和[原容量结果](../../.trellis/tasks/09-24-commercial-operations-acceptance/post-authorization-validation.json)保持各自时间与范围。Draft PR 与任务仍保持待最终放行，不以技术复核代替客户签署。

rc.6 公网 GET 已验证三个指纹资源的 immutable/gzip 与实际镜像内容 SHA，HTML no-store、缺失指纹文件 404 + no-store。rc.5 的首次主 JS 连接中断与缺失文件 CDN 四小时缓存均保留；补修前后实际 nginx HTTP 测试分别失败、通过。静态加载改善不替代服务器受理 p95 和容量验收。

rc.7 批量入口移除重复完整读表，隔离 PostgreSQL 的单行批量 SELECT 从33减为25，与单行入口一致。已完成任务各4次、交替顺序的线上重放：批量中位数1669.773→1291.637ms；切换后单行中位数1291.082ms。前后共20次 HTTP，没有新增任务、供应商调用或消费。测量走 API Pod 回环到实际服务进程，属于完成任务重放；原容量采样使用单行入口，不能用此结果关闭新任务受理两秒目标。

rc.8单行/批量进一步降至23次SELECT。后续只读诊断发现API仅一个连接，隔离4并发对照中4连接减轻排队但仍有数据库读取波动；文档SQL服务器执行约1–2ms，普通往返约49–53ms。API四连接及503/service_busy候选已通过2094非integration/23子测试与静态检查，尚未部署；原配置和容量结果不变。纯端口切换无明显收益，生产仍使用5432。
