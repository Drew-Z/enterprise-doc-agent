# 单一来源协议 v7 交付记录

## 正式变更

- `SelectionInput` 显式投影所需字段，内部UUID/hash保留在授权快照与引用目录；文件名/版本/适用范围保留，来源标签可区分同名文件。
- `SelectionDraft` 独立于公开响应，只生成一次结构化前提。服务器由unmet/unknown按序去重形成conditions，met不列待办；不修补模型错误、别名匹配或重试。
- 合成collector升级run-v2，实际wire输入与内部sourceInput分别留存，scorer验证完整绑定及已接受结果，保留v1评分和所有旧失败。
- 更新HTTP模型fixtures、数据库/CSV回归、规范、README和完整评测文档。公开模型、数据库、身份及生产路由未改。

## 验证

三组红例均已记录：内部身份泄露、重复条件依赖、旧collector未分离输入；对应修复通过。

- 1653项非集成通过，359项deselected，23项subtests；含历史未提交测试，远端CI另验精确提交。
- 25项PostgreSQL通过；3项售前浏览器、3项真实入库浏览器通过，测试资源自动清零。
- Ruff通过、553文件格式通过；Mypy235个源文件通过（含collector/scorer）。
- 四份历史v5/v6评分离线复算保持完全相同。
- 一条新CSV断言最初错误要求已复核导出保留原条件，已按既有契约分开核验draft/reviewed。入库浏览器首启因临时输出目录尚不存在而失败，创建本阶段专属目录后3项通过。原失败日志保留。
- 只读线上核查五个deployment均1/1就绪，镜像与已验证v0.1.44完全一致。

详细日志及说明见 `protocol-v7-validation.json`；最终提交/CI、恢复点和清理结果写入 `protocol-v7-final-review.json`。

## 冻结真实试验与决定

提示词SHA `11542919c2ebb588715fa788def38325b9e4384883fdb277bb6b9046d8dce55f`；原始H3结果SHA `97e10ebc3c2919a5970d8490b236645d1b55cc18b0ec543d03cb7deb55894d43`。冻结文件见 `protocol-v7-freeze.json`，逐条审阅见 `evaluation/presales_quality_v7.review.json`。

实际6次，4份有效中文草稿且分类/正文通过，2次在等待响应头阶段达到120秒。引用5/5逐字有效，必要锚点4/9（含失败）。R3冲突关系和R5未知验收均没有响应，不能声称语义已修复或真实条件投影已验证。按预定规则停止其余18次，H4仍未尝试，没有重试/重放/改gold。

部分用量23288token，完整用量和金额未知。4份完整响应共报告12767个reasoning token，其中一条completion超过请求max_tokens；这不能解释两条无正文调用的根因，也不是已核验账单。下一轮先核查路由等待时间与预算字段兼容性，再冻结新有界试验。

候选继续Draft，未合并、打标签或部署；本阶段的代码和流程改进保留。父任务仍in_progress，整体产品质量门槛尚未完成。

## 恢复与工作区

复用 `D:\Agent\codex\backups\tasks\20260921T060311.970Z-saas-public-pilot-integration`，阶段 `presales-single-source-protocol`。15份干净跟踪文件可由准确基线提交恢复；修改前task.json有实体快照，hash已核验。保留2915项历史脏/未跟踪清单及四个worktree，不清理其他任务。专属临时目录在最终核查后按精确清单清理，正式评测与本地验证记录保留。
