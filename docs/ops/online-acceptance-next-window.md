# 当前部署、回退与验收边界

2026-09-29 UTC：当前部署为 **v0.1.45-rc.4 / cdc88e1ca43e812490ef22901d624bdacde014b0**，数据库 `20260924_0031`。统一用户入口见[最终验收](final-project-acceptance.md)，本轮实测见[用户体验](user-experience-acceptance.md)和[机器记录](../../.trellis/tasks/09-24-commercial-operations-acceptance/ux-release-validation.json)。

## 发布与回退

[发布流水线 36565232896](https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/36565232896)四镜像及清单成功；清单 SHA-256 为 `b68dee4439831dd9337b375c32f063cb475ce6b92d22981a81e2847fc84de695`。56 份文件哈希、镜像及证明主题复核通过；密码学签名验证由该 CI 执行。

以下位于 `ghcr.io/drew-z/enterprise-doc-<组件>`，使用完整 `image@sha256`：

| 组件 | 当前 rc.4 | 回退 rc.3 |
| --- | --- | --- |
| api | `sha256:12afc6d2508d3841dd482474ce9257024850c66a745f93242a5e305dbf243352` | `sha256:3e3bbd3858bcf1731f00628d893431808c80d33961da1217ea7324ce1e31b69d` |
| worker | `sha256:6813eb25e47bdf30c49b024c0e69d66740d9daf580e82aa603e68877626643ad` | `sha256:f39a247ff1b4821364046fe383fdbd46d78a192121d45fc3e8751fbc6505a26c` |
| consumer | `sha256:8839431008975269d477844ae9db057e0bbf833b994f4baa72460a3d7493e7fa` | `sha256:f18e9b2a6235dc629dd95b384e09dad67f9033238ee3e1b43c53bf8b755a6534` |
| web | `sha256:952a5d12ba07826b4f54df6385c9f55fb1a5cbc736d06377966912faa08fc6b1` | `sha256:62257d676dd580caaea728e5e7aa56656206d48cd9421d7e61a37c302d6a88d8` |

独立守卫在 84.912 秒内完成镜像切换。API、Worker、Consumer、Web 和 Redis 均就绪；配置、凭据和 0031 不变。四个 GitHub staging 回退变量已读回，其他变量未改。远端临时运行目录与 11 个文件已清理，原恢复输入集中本地保存。当前回退引用存在不等于本窗口执行过 rc.4→rc.3 故障演练；数据库不得降回 0027。

## 实际业务配置

- 主路 `grok-4.7`，备用 `grok-4.6`；三后端进程一致，两个模型名不证明供应商独立。
- 售前 `primary`、后台和自动切换启用、并发 1。每行执行上限 150 秒，排队上限 900 秒；首次模型为备用路预留时间。
- 原 pilot 于北京时间 2026-09-29 18:57:59 到期，不自动延期或重置。本轮另建四任务、30 分钟的有限合成周期，仅用于诊断。

## 已执行与仍待完成

原 Agent 的真实上传、检索/MCP、生成、产物和一次计量，以及同键重放已通过。原售前后台离线完成、技术复核、CSV 和账本证据继续有效。rc.4 再次核验 Agent 结果/引用/下载/刷新；静态字节来自运行中的 Web 镜像，业务 API 真实走公网，认证使用已授权 staging 自动化适配。公网静态文件和售前 CSV 在本机代理上超时，失败保留，不要求用户重复登录排查。

新四任务的上传、解析、检索、生成、重放、复核、CSV 和一次结算全通过，模型 4、向量 12 次。受理中位数 2,158.8 ms、四样本 p95 5,663.7 ms，2 秒目标未通过。该诊断不能关闭原两轮 160 任务容量失败：旧 9 通过/4 失败/147 未执行保持不变。新四次均主路成功，不声称线上真实供应商切换已覆盖。

两项 Windows 无窗口运维任务继续运行，备份实际还原和对象验证、有限快照轮换保持。个人电脑停机后的连续运行、独立业务/发布审核、值班及整机 RPO/RTO 仍需要实际完成；不配置常驻备用机不豁免按需恢复验证。

## 对账与历史记录

应用侧调用/业务账本保留实际调用与一次结算；未知费用保持 null。模型账户查询只有累计配额单位，向量旧账户端点返回 410；不能据此换算币种和逐笔金额。此前两次逻辑查询实际包含一次重定向，共三次 HTTP，纠正回执单独保存，原证据未覆盖。

旧 [Agent 验收](online-business-acceptance.md)、[售前验收](online-presales-browser-acceptance.md)、[rc.3 发布记录](../../.trellis/tasks/09-24-commercial-operations-acceptance/final-delivery-validation.json)和[原容量结果](../../.trellis/tasks/09-24-commercial-operations-acceptance/post-authorization-validation.json)保持各自时间与范围。Draft PR 与任务仍保持待最终放行，不以技术复核代替客户签署。
