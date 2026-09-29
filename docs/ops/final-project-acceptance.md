# 项目最终验收入口

更新：2026-09-29 UTC。当前可使用版本为 **v0.1.45-rc.3**，源码
`58ef13dc505645529c54d5d14db7a076fdb4f8c9`，数据库 `20260924_0031`。
功能修复已发布；完整商业验收仍有下列明确缺口，任务保持 `in_progress`。
脱敏机器记录见[本轮核验](../../.trellis/tasks/09-24-commercial-operations-acceptance/final-delivery-validation.json)。

## 已交付并验证

| 项目 | 实际结果 | 边界 |
| --- | --- | --- |
| Agent | 真实上传、解析、检索/MCP、模型生成、持久产物与一次计量通过；相同键重放不增账本；rc.3 页面结果、引用、下载和刷新通过 | 本批模型 1、向量 2；不是容量或独立语义审核 |
| 售前 | 原表一次后台生成，浏览器离线 180 秒期间完成；后续重开/刷新、技术复核、CSV、一次消费通过 | 技术复核不是客户批准；原窗口自动重连未通过 |
| 两个修复 | Agent 接受合法 diagnosticCode；v9 按证据的限定范围判断事实，不把测试范围误作待批准条件 | v9 一次真实定向回归通过；错误原稿与失败样本保留 |
| 发布 | 四镜像扫描、SBOM、签名和证明成功；56 文件哈希复核；独立守卫内 95.583 秒完成切换 | 当前 rc.2 回退引用已保存；未声称本窗口执行过故障回退 |
| 备份 | 同一只读数据库 snapshot 导出并在独占 PG 实际恢复，56 表/79,547 行；1,588 对象逐字节校验 | 应用 public schema；Supabase 管理配置、角色和整机未涵盖 |
| 定时运维 | Windows 队列/备份任务实际重复运行；Cloudflare 监控和 watchdog 的真实分钟观察、配置读回通过 | 本机在线、登录、SSH、Docker 和 3 GiB 阶段上限约束 |
| 通知 | 原 readiness 故障/恢复测试两封已由用户确认实收；扩展范围沿用限定目的地址和去重逻辑 | 没有代填新增实收、值班或 24×7 SLA |

主链路证据在[Agent 验收](online-business-acceptance.md)、[售前验收](online-presales-browser-acceptance.md)
和[当前版本与镜像](online-acceptance-next-window.md)。本轮新增供应商调用为 **0**。

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
| 当前 4C4G 两轮容量 | 按[冻结提案](4c4g-business-capacity-plan.md)审核 160 任务矩阵、隔离测试企业/周期及供应商请求预算；当前 local-only CLI 不能直接指向线上，外部执行适配及同主机完整遥测仍需完成 |
| 供应商金额 | 提供对应已发生调用时间段的用量/账单，至少有模型、时间、tokens、金额与币种；应用账本已导出，缺少金额不填零 |
| 独立业务与发布审核 | 实际审核人确认来源契约、回答、用户流程和候选发布；目前 Draft PR 不能表示独立批准 |
| 长期备份留存 | 当前 3 GiB 仅是有界运行，不能无限保留每轮约 23.7 MB dump；需要批准具体轮换范围或指定可用备份存储，历史及被引用恢复点保持 |
| 值班与整机 RPO/RTO | 确认值班人/响应时间与可重建的演练目标，实际测量 RPO≤300 秒/RTO≤1800 秒；不要求常驻备用机，但需要明确的按需恢复资源 |

来源/问题参考由助手编写的质量评测，不能改称独立 gold；历史 4/5 和引用契约差异保留。
原位读取的额外线上检查先后遭遇页面超时、静态资源代理中止与 SOCKS 读取失败，均在进入
新的业务写入前停止。已经通过的本地原位重试、线上离线后台完成与重开结果分别保留。

## 运行与恢复记录

私有交付物集中于原恢复组的 `final-delivery-evidence`，包括发布清单、执行器、源快照、
对象引用、CDP 截图/CSV、调度 XML、D1 状态与清理回执。凭据只在本地以当前用户 DPAPI 保存，
文件 ACL 限当前用户、SYSTEM 与管理员；不进 Git。

Windows 任务 `DocAgent-QueueHeartbeat` 和 `DocAgent-VerifiedBackup` 均每分钟尝试启动，
`IgnoreNew` 防止同类重叠；备份通常约 95–102 秒，观测到一次 137 秒并据此修正触发周期。
失败不会覆盖成功基线、自动重试未知业务或删除旧备份。停止任务可用以下 PowerShell 命令；
停止后 Cloudflare 将按真实过期状态告警，不能把停止采样当作业务健康。

```powershell
Disable-ScheduledTask -TaskName 'DocAgent-VerifiedBackup'
Disable-ScheduledTask -TaskName 'DocAgent-QueueHeartbeat'
```

Cloudflare 主监控检查 readiness、队列来源 120 秒/备份源快照 300 秒/另一监控观察 180 秒。
watchdog 单独调度并监视主监控，两者共享 Cloudflare/D1，不能覆盖平台整体故障。
监控回退使用已保留的配置和版本；保留 D1 事件，不重置 unknown/attempting 通知。

本轮 30 个镜像中转临时文件及空目录已删除；运行/回退镜像缓存、历史恢复组和原 2,993 项
工作区未提交状态保留。临时观察 Worker 已移除，正式 monitor、watchdog、邮箱和 D1 保留。
