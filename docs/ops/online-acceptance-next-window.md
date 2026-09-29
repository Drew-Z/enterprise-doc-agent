# 当前部署、回退与验收边界

2026-09-29 UTC：当前部署为 **v0.1.45-rc.7 / 587d72713711ed278a166f083e1467f4e7efbb32**，数据库 `20260924_0031`。统一用户入口见[最终验收](final-project-acceptance.md)，本轮实测见[机器记录](../../.trellis/tasks/09-24-commercial-operations-acceptance/rc7-release-validation.json)。

## 发布与回退

[发布流水线 36625659126](https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/36625659126)四镜像及清单成功；清单 SHA-256 为 `42dc1fa04792e97f1f8f70022e9eff513a58e619b3dd1f38dbe27d48d2b86b3b`。56 份文件哈希、镜像及证明主题复核通过；密码学签名验证由该 CI 执行。

以下位于 `ghcr.io/drew-z/enterprise-doc-<组件>`，使用完整 `image@sha256`：

| 组件 | 当前 rc.7 | 回退 rc.6 |
| --- | --- | --- |
| api | `sha256:1bd29a0ab8ec4427a19099e8f72245c4a7570c23145350b1a0db070d927a7c7e` | `sha256:192f8b5e198da6232c7762603f10f98d8e9bd263ff644ffa015839dfb10a8626` |
| worker | `sha256:1df49907734385623bbce20c6a8901795a2002bff1705c76b14814afa962fa04` | `sha256:ae9aace76fda33cb9f0154aca9544dd04c71fd09a67766cf9a917572c1a22f9b` |
| consumer | `sha256:c62be34aa8cfe5a44e1fd15fc3073829d9bd8062112f889f7f5769952bdb48d9` | `sha256:4ea4ba6355a90a931482797e7ac335e0cc3cde5ef689be7effb3a8ffd10cc77e` |
| web | `sha256:101b0019f2f1da6924bcbe0a546db5176f1726a7aa0e083a4928ce9fd54fd7ad` | `sha256:74e0619ff5b4d3bfd9de7a9c577d9d88f33ec0d35616d69daf6edc6de8733619` |

独立守卫在 94.937 秒内完成镜像切换。API、Worker、Consumer、Web 和 Redis 均就绪；配置、凭据和 0031 不变。四个 GitHub staging 回退变量已读回，其他变量未改。远端临时运行目录与 11 个文件已清理，原恢复输入集中本地保存。当前回退引用存在不等于本窗口执行过 rc.7→rc.6 故障演练；数据库不得降回 0027。

## 实际业务配置

- 主路 `grok-4.7`，备用 `grok-4.6`；三后端进程一致，两个模型名不证明供应商独立。
- 售前 `primary`、后台和自动切换启用、并发 1。每行执行上限 150 秒，排队上限 900 秒；首次模型为备用路预留时间。
- 原 pilot 于北京时间 2026-09-29 18:57:59 到期，不自动延期或重置。本轮另建四任务、30 分钟的有限合成周期，仅用于诊断。

## 已执行与仍待完成

原 Agent 的真实上传、检索/MCP、生成、产物和一次计量，以及同键重放已通过。原售前后台离线完成、技术复核、CSV 和账本证据继续有效。rc.4 再次核验 Agent 结果/引用/下载/刷新；静态字节来自运行中的 Web 镜像，业务 API 真实走公网，认证使用已授权 staging 自动化适配。该阶段公网静态文件和售前 CSV 在本机代理上超时，失败保留。随后 rc.5 直接使用公网静态文件与真实 API 的 CDP 验证 Agent 结果/引用/下载/刷新、售前 CSV/离线后重开全部通过，无新业务提交；rc.6 的 HTML/JS/CSS 哈希与受测版本完全相同。

新四任务的上传、解析、检索、生成、重放、复核、CSV 和一次结算全通过，模型 4、向量 12 次。受理中位数 2,158.8 ms、四样本 p95 5,663.7 ms，2 秒目标未通过。该诊断不能关闭原两轮 160 任务容量失败：旧 9 通过/4 失败/147 未执行保持不变。新四次均主路成功，不声称线上真实供应商切换已覆盖。

两项 Windows 无窗口运维任务继续运行，备份实际还原和对象验证、有限快照轮换保持。个人电脑停机后的连续运行、独立业务/发布审核、值班及整机 RPO/RTO 仍需要实际完成；不配置常驻备用机不豁免按需恢复验证。

## 对账与历史记录

应用侧调用/业务账本保留实际调用与一次结算；未知费用保持 null。新读取的 Windhub 52条账单合计300,694配额，与令牌累计用量一致，显示配置为 CUSTOM / 500000配额每单位；不能标成人民币或美元。旧应用 ID 与网关账单 ID 精确匹配为0，现已定位到 x-oneapi-request-id 与上游 x-request-id 的区别，候选修复待上线实测。向量旧账户端点已退役，金额尚未核实。此前两次逻辑查询实际包含一次重定向，共三次 HTTP，纠正回执单独保存，原证据未覆盖。

旧 [Agent 验收](online-business-acceptance.md)、[售前验收](online-presales-browser-acceptance.md)、[rc.3 发布记录](../../.trellis/tasks/09-24-commercial-operations-acceptance/final-delivery-validation.json)和[原容量结果](../../.trellis/tasks/09-24-commercial-operations-acceptance/post-authorization-validation.json)保持各自时间与范围。Draft PR 与任务仍保持待最终放行，不以技术复核代替客户签署。

rc.6 公网 GET 已验证三个指纹资源的 immutable/gzip 与实际镜像内容 SHA，HTML no-store、缺失指纹文件 404 + no-store。rc.5 的首次主 JS 连接中断与缺失文件 CDN 四小时缓存均保留；补修前后实际 nginx HTTP 测试分别失败、通过。静态加载改善不替代服务器受理 p95 和容量验收。

rc.7 批量入口移除重复完整读表，隔离 PostgreSQL 的单行批量 SELECT 从33减为25，与单行入口一致。已完成任务各4次、交替顺序的线上重放：批量中位数1669.773→1291.637ms；切换后单行中位数1291.082ms。前后共20次 HTTP，没有新增任务、供应商调用或消费。测量走 API Pod 回环到实际服务进程，属于完成任务重放；原容量采样使用单行入口，不能用此结果关闭新任务受理两秒目标。
