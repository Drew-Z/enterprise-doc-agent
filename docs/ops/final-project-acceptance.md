# 项目最终验收入口

更新：2026-09-30 UTC。当前部署版本为 **v0.1.45-rc.9**，源码
`72d01d2f949243a6725b510fe27079f160746101`，数据库 `20260924_0031`。
功能修复已发布；完整商业验收仍有下列明确缺口，任务保持 `in_progress`。
前阶段脱敏机器记录见[发布核验](../../.trellis/tasks/09-24-commercial-operations-acceptance/final-delivery-validation.json)。
用户已授权后续操作；新增实测和弹窗修复见[授权后收尾](post-authorization-closeout.md)。
最新[用户体验与故障恢复](user-experience-acceptance.md)修复已部署：区分即时反馈、服务器受理、队列与模型时间，给备用路保留执行时间，恢复 Agent 丢失的回执，减少响应表读取的数据库往返。实际发布及新四任务诊断见[rc.4 核验](../../.trellis/tasks/09-24-commercial-operations-acceptance/ux-release-validation.json)。四任务全部成功，但受理 2 秒目标仍未通过。

rc.5 已通过直接公网 CDP 的 Agent 与售前只读流程。rc.6 补充显式禁止 CDN 缓存缺失静态资源，当前版本及完整边界见[静态发布核验](../../.trellis/tasks/09-24-commercial-operations-acceptance/static-release-validation.json)。

rc.7 移除批量受理前的重复整表读取，真实数据库回归中单行批量 SELECT 从 33 降至25。线上已有成功任务的批量重放中位数从 1,669.773 ms 降至 1,291.637 ms，同轮单行重放为1,291.082 ms；原稿和账本不变。此测量不包含新任务受理，也不替代完整容量验收。见[rc.7 核验](../../.trellis/tasks/09-24-commercial-operations-acceptance/rc7-release-validation.json)。

rc.8 将单行受理 SELECT 从25降至23，并发布网关请求标识优先识别。新160任务计划按失败阈值停止：30通过、5失败、125未执行；32个受理样本p50 3,535.434ms、p95 7,474.234ms，2秒目标未通过。记录见[rc.8 核验](../../.trellis/tasks/09-24-commercial-operations-acceptance/rc8-release-validation.json)。只读对照确认单连接池产生并发排队；API四连接和503繁忙响应的候选已通过本地检查，尚待发布后验证，不能覆盖这次失败。

rc.9 已部署 API 四连接与繁忙响应修复。独立12任务诊断为0通过、4失败、8未执行，受理p95 5,376.727ms；四份上传/解析/检索完成，但售前Job未被领取，模型请求为0、向量8。旧Worker曾因连接池超时退出，当前进程后台与遥测停滞但存活探针仍返回成功。新增进度检查候选已通过本地验证，尚待发布；具体驱动或网络根因未确认。见[rc.9 核验](../../.trellis/tasks/09-24-commercial-operations-acceptance/rc9-release-validation.json)与[Worker恢复候选](../../.trellis/tasks/09-24-commercial-operations-acceptance/worker-progress-validation.json)。当前售前新生成不能视为可用。

Worker候选的Quality通过，但容器扫描检出PyJWT 2.13.0的1项严重、5项高危漏洞。最低版本已提高至2.14.0，锁定当前2.15.1，其他包版本不变；72项认证测试、2127项非integration及23项子测试通过。新候选仍须通过容器扫描和实际发布，线上版本未因此改变。见[依赖修复核验](../../.trellis/tasks/09-24-commercial-operations-acceptance/pyjwt-remediation-validation.json)。

## 已交付并验证

| 项目 | 实际结果 | 边界 |
| --- | --- | --- |
| Agent | 原真实上传、解析、检索/MCP、生成和一次计量通过；rc.5 公网 CDP 再次验证答案、引用、594 字节下载与刷新，恢复后无错误提示 | 静态资源和业务 API 均走真实公网，只有认证响应使用已授权短期 staging 适配；rc.6 的 HTML/JS/CSS 与受测 rc.5 逐字节相同 |
| 售前 | 原表一次后台生成，浏览器离线 180 秒期间完成；后续重开/刷新、技术复核、CSV、一次消费通过 | rc.5 公网 CDP 验证原表 CSV、离线后重开且表未改变；技术复核不是客户批准，原窗口自动重连失败保留 |
| 两个修复 | Agent 接受合法 diagnosticCode；v9 按证据的限定范围判断事实，不把测试范围误作待批准条件 | v9 一次真实定向回归通过；错误原稿与失败样本保留 |
| 发布 | rc.9 的56份发布证据核验通过，90.735秒完成切换，五服务在发布后就绪；公网readiness和首页通过 | 回退rc.8；API池4/0、后台1/0，共享配置、凭据、数据库不变；后续Worker停滞另记，发布成功不代表业务通过 |
| 真实上游恢复 | 本轮两个主路错误由备用路成功恢复，各两次模型调用、一次业务消费，复核与CSV通过 | 另一次无效模型输出仍失败并保留；不保证所有供应商错误都能恢复 |
| 新四任务诊断 | 四个 59 字节合成 TXT 的上传、解析、检索、生成、同键重放、技术复核、CSV 和一次结算全部通过；模型 4 次、向量 12 次 | 单并发小样本，未触发真实供应商故障；不是两轮容量或独立业务审核 |
| 备份 | 同一只读数据库 snapshot 导出并在独占 PG 实际恢复，56 表/79,547 行；1,588 对象逐字节校验 | 应用 public schema；Supabase 管理配置、角色和整机未涵盖 |
| 定时运维 | 队列/备份任务已改用 pythonw 与无控制台子进程，连续运行通过；增加已验证快照轮换及新对象共享池 | 仍依赖本机在线、登录、SSH、Docker；失败/冻结/被引用快照保留，3 GiB 总上限保持 |
| 通知 | 原 readiness 故障/恢复测试两封已由用户确认实收；扩展范围沿用限定目的地址和去重逻辑 | 没有代填新增实收、值班或 24×7 SLA |

主链路证据在[Agent 验收](online-business-acceptance.md)、[售前验收](online-presales-browser-acceptance.md)
和[当前版本与镜像](online-acceptance-next-window.md)。发布后只读验证阶段新增供应商调用为 **0**；
原容量执行另计，为模型18次、向量36次；rc.4诊断为模型4次、向量12次；rc.8容量为模型34次、向量100次。未知金额不填零。

## 用户最后一次功能验收

1. 打开 [应用](https://agent.playlab.eu.cc/)，使用原有会话与“DocAgent 演示空间”。
   会话有效就直接进入；仅在应用要求认证时使用已有 GitHub 登录，不需要重复做 OAuth 验收。
2. 在[售前工作台](https://agent.playlab.eu.cc/#/presales)打开
   `Single-row browser recovery acceptance 20260929`，查看 R1 的原稿、引用及技术复核。
   原文为 `The evidence retention period is thirty days.`，支持的是这一合成范围内的事实。
3. 点击“导出已复核 CSV”，核对答案、状态和引用；刷新后仍是同一张表。此步骤不生成新任务。
4. 在[Agent 页面](https://agent.playlab.eu.cc/#/agent-runs)检查原浏览器已保存的成功任务：
   答案应包含 `thirty days`，引用能对应上传原句，下载草稿并刷新仍能读取。
   原任务 ID 以私有验收记录为准；如果当前浏览器没有保存该任务，使用已交付的截图/产物核对，
   不为找回历史任务重新生成。当前页面依赖该浏览器的本地恢复记录。
5. 记录“通过”，或指出具体页面、动作、实际与预期差异。业务审核须由实际审核人记录，助手不代签。

这是对已有结果的只读验收。原 pilot 于 **北京时间 2026-09-29 18:57:59** 到期；
原周期不会自动延期或重置。新生成需要有效的有限周期与独立请求预算。

## 仍未完成的放行项

| 项目 | 下一步需要的具体输入或执行 |
| --- | --- |
| 当前 4C4G 两轮容量 | rc.9十二任务诊断为0/4/8，受理p95 5.377秒，售前领取停滞；先验证Worker恢复，再独立复测。rc.8的30/5/125与原9/4/147保持，不能替换失败分母 |
| 供应商金额 | Windhub最新84条账单合计486,289配额，与令牌累计一致；CUSTOM不是已核实币种。rc.8共34模型记录仅2个请求ID精确匹配，且均为零配额错误记录；成功调用仍未对上。主备地址与密钥不同，主账户账单不覆盖全部备用消费；向量费用亦未知 |
| 独立业务与发布审核 | 实际审核人确认来源契约、回答、用户流程和候选发布；目前 Draft PR 不能表示独立批准 |
| 长期备份运行 | 快照轮换已接入；最近五个不同内容快照、冻结/失败/引用对象恢复点保留。新对象单独共享，避免持续锁住每次数据库 dump。本机注销、睡眠、停机时的持续运行仍未解决，不能承诺 24×7 |
| 值班与整机 RPO/RTO | 本地完整Ubuntu VM和K3s系统组件已就绪；数据库/对象/应用尚未恢复。继续演练并测量RPO≤300秒/RTO≤1800秒；本地结果不覆盖云申请和公网切换，值班责任仍待确认 |

来源/问题参考由助手编写的质量评测，不能改称独立 gold；历史 4/5 和引用契约差异保留。
原位读取的额外线上检查先后遭遇页面超时、静态资源代理中止与 SOCKS 读取失败。rc.4 本机公网静态加载又出现超时，SSH TCP 转发被主机配置拒绝，均未修改主机安全配置。改用运行中容器的已校验静态字节后 Agent 验证通过；原售前表读取通过，但本机代理 CSV 请求超时，因此该 rc.4 窗口的完整售前 CDP 未通过。新四任务的真实 API 导出全部通过。随后 rc.5 使用实际公网页面与 API 的 CDP 完整通过 Agent/售前只读验收，未替换静态字节；离线错误文档触发测试初始化脚本的 localStorage SecurityError，重开后恢复，原错误日志保留。已有本地恢复与各阶段结果分别保留。

## 运行与恢复记录

私有交付物集中于原恢复组的 `final-delivery-evidence`、`post-authorization-evidence`、`ux-release-evidence`、`static-release-evidence`、`static-error-release-evidence`、`acceptance-completion-evidence` 和 `rc8-release-evidence`，包括发布清单、执行器、源快照、
对象引用、CDP 截图/CSV、调度 XML、D1 状态与清理回执。凭据只在本地以当前用户 DPAPI 保存，
文件 ACL 限当前用户、SYSTEM 与管理员；不进 Git。

Windows 任务 `DocAgent-QueueHeartbeat` 和 `DocAgent-VerifiedBackup` 均每分钟尝试启动，
`IgnoreNew` 防止同类重叠；备份通常约 95–102 秒，观测到一次 137 秒并据此修正触发周期。
已授权轮换只删除精确计划中的已验证旧快照；冻结、失败和仍被引用的实体保持。失败不会覆盖成功基线或重试未知业务。停止任务可用以下 PowerShell 命令；
停止后 Cloudflare 将按真实过期状态告警，不能把停止采样当作业务健康。

```powershell
Disable-ScheduledTask -TaskName 'DocAgent-VerifiedBackup'
Disable-ScheduledTask -TaskName 'DocAgent-QueueHeartbeat'
```

Cloudflare 主监控检查 readiness、队列来源 120 秒/备份源快照 300 秒/另一监控观察 180 秒。
watchdog 单独调度并监视主监控，两者共享 Cloudflare/D1，不能覆盖平台整体故障。
监控回退使用已保留的配置和版本；保留 D1 事件，不重置 unknown/attempting 通知。

rc.4、rc.5、rc.6、rc.7 各自的 30 个镜像中转临时文件及空目录均已删除；运行/回退镜像缓存、历史恢复组和原 2,993 项
工作区未提交状态保留。临时观察 Worker 已移除，正式 monitor、watchdog、邮箱和 D1 保留。

rc.8另清理29个本地OCI传输文件（506,819,616字节）及远端11个运行文件。诊断脚本、失败日志和容量数据是验收证据，继续保留。脱离个人电脑的加密备份及按需恢复执行提案已保存，尚未上传敏感备份、安装服务器备份服务或创建恢复主机。

rc.9的30个本地OCI传输文件及空目录已核对哈希后清理，释放506,837,790字节；运行镜像、恢复组、VM与原2,993项工作区状态保留。

连接池及发布守卫的2122项非integration、23项子测试和744项部署/API回归属于rc.9发布前检查；实际发布和失败诊断已另记。Worker进度候选通过2127项非integration/23子测试及33项定向检查，尚未部署。恢复点继续使用原集中恢复组；旧4C8G资料不替代本地完整VM的新演练。
