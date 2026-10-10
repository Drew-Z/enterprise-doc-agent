# 公开 VSAQ 问卷流程试用与扩展资料

日期：2026-10-11（Asia/Shanghai；原始回执使用 2026-10-10 UTC）。

已收集四套真实公开问卷模板、19 份 GitLab 官方资料，整理出24个可追溯用例。
其中8题通过已部署产品完成上传、入库、导入、生成、证据浏览、署名补写/复核、
重新读取和 XLSX/CSV 导出。原始生成结果为3条草稿、5条失败；5条失败由 Codex
通过人工答复入口署名补写，8条均有单独复核记录。16题只完成扩展准备，尚未执行。

这是使用公开资料的 Codex 辅助流程试用。它没有真实客户问卷提交方、独立客户
审批或工时基线，也未完成本轮浏览器点击验收。当前结果不能证明无人值守生成可靠。

## 可直接使用的交付物

- [完整资料与结果包（ZIP）](D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness/fallback-model-evidence/public-trial-20261011-materials-and-results.zip)
- [24题扩展工作簿](D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness/fallback-model-evidence/public-trial-20261011-expansion-ready.xlsx)
- [8题复核结果 XLSX](D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness/fallback-model-evidence/public-trial-20261011-reviewed.xlsx)及[CSV](D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness/fallback-model-evidence/public-trial-20261011-reviewed.csv)
- [原始模型交付 XLSX](D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness/fallback-model-evidence/public-trial-20261011-original-draft.xlsx)
- [使用说明与19份资料来源](D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness/fallback-model-evidence/public-trial-20261011-README.md)
- [逐题原始输出评估](D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness/fallback-model-evidence/public-trial-20261011-raw-assessment.json)与[机器核验结果](D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness/fallback-model-evidence/public-trial-20261011-verification.json)

ZIP包含原始问卷/许可证、官方网页原始字节、文本阅读副本、来源索引、参考准则、
原始与复核后导出、扩展工作簿及逐题资料映射。URL、UTC获取时间、SHA-256和适用
范围均保留；19份官方网页中的 CC BY-SA4.0 许可链接均已核验，包内保留署名和
提取转换说明。问卷遵循 Apache2，中文是参考译文。

|交付物|SHA-256|
|---|---|
|ZIP（2,094,230字节）|`71a2637cfd4705ed4b42c630b5608d9fb1ab14a62fed0974d9706e8ae0f84f74`|
|24题扩展工作簿|`c3f84344a6e657535b5558bf5fbd5b0df0fea75f11041df002943b65642ca564`|
|8题复核 XLSX|`80761e8df4952f764a5206ca843a0556967e5c9c704d9fb767b5d2d9eec0ad4e`|

## 真实来源与业务范围

问卷来自 [Google VSAQ](https://github.com/google/vsaq/tree/366d670e7e2a544166f74182e1093e6162786eb7/questionnaires)，
固定提交 `366d670e7e2a544166f74182e1093e6162786eb7`。保留安全与隐私、基础设施、
Web应用、物理与数据中心四个原始模板的题号、英文、选项和显示条件。该项目已归档，
不能将其称为现行认证标准，Excel工作副本也没有实现其交互分支执行器。

被评估对象固定为 GitLab.com 共享 SaaS。核心来源包括
[ISMS](https://handbook.gitlab.com/handbook/security/isms/)、
[培训标准](https://handbook.gitlab.com/handbook/security/security-assurance/governance/sec-training/)、
[备份政策](https://handbook.gitlab.com/handbook/engineering/gitlab-com/policies/backup/)、
[事件响应](https://handbook.gitlab.com/handbook/security/product-security/vulnerability-management/incident-response-guidance/)、
[漏洞管理](https://handbook.gitlab.com/handbook/security/product-security/vulnerability-management/)和
[加密政策](https://handbook.gitlab.com/handbook/security/product-security/vulnerability-management/encryption-policy/)。
Dedicated 数据驻留与 Self-Managed 备份文档明确标为不适用，不能填补共享 SaaS 的事实缺口。

最初13份供应商资料构成本轮来源池；其中六个冻结资料包实际上传，合计68,767字节，
入库67个分块。后续补充的角色、密码、会话、日志标准、物理资产和审计日志政策六份
资料只用于扩展准备，没有加入已冻结的线上问卷。参考判定准则未发送给生成模型。

## 8题实际结果

|原题ID|原始生成|复核后结论与保留缺口|
|---|---|---|
|`secprog`|主模型 `draft_contract`，回退超时|有正式 ISMS；客户项目覆盖范围和共享责任仍需确认。署名补写。|
|`secprog_train_tvc`|主模型草稿|承包商/临时服务人员安全培训有依据；实习生适用性及明确隐私培训要求不足。|
|`secprog_incidents_procedures`|主模型拒绝，回退草稿|书面事件响应流程有充分依据；不扩展为合同通知时限或演练完成证明。|
|`servers_backup_test`|主模型拒绝，回退超时|数据库/仓库有部分恢复验证；不能确认全系统端到端恢复测试。署名补写。|
|`servers_backup_frequency`|主模型拒绝，回退 `basis_quote`|固定小时备份不能证明客户协商或定制频率程序。署名补写。|
|`testing_pentests_companies`|主模型拒绝，回退 `basis_quote`|年度第三方测试要求不能提供最近三次执行公司名称。署名补写。|
|`application_webvuln_crypto_details`|主模型拒绝，回退超时|加密政策存在；算法、密钥及签名细节仍缺。署名补写。|
|`dc_countries`|主模型拒绝，回退草稿|Dedicated地区资料不能建立 GitLab.com 完整国家清单。|

共15次生成调用：8次主模型、7次正常配置回退；3次形成有效草稿、12次失败。
7次主模型拒绝的安全诊断均为 `draft_contract`。回退为2次草稿、2次 `basis_quote`
拒绝、3次超时。每题仅一次产品生成尝试，没有重新生成或追加修复调用。

3条有效原稿的语义观察：培训题保留了核心隐私/实习生证据缺口，但答案达到1,993字，
把题号、译文和显示元数据当成了答复方面；事件流程题达到冻结的实质准则；国家题
安全地保留未知，但没有充分解释产品范围不匹配。其余5条没有有效草稿，不能作为
语义成功样本评分。被拒绝的供应商完整响应未保留，现有诊断不足以解释每次失败的
内部原因；元数据问题是已接受培训草稿中的直接观察，不能据此归因全部失败。

通过产品人工答复接口新增5条带操作者身份的补写，再对全部8条保存署名复核，
引用同一冻结资料包的真实分块。最终1条 `supported`，7条 `insufficient_evidence`。
复核删除重复或属于元数据的前置项，保留实际资料缺口；原始草稿、生成尝试和失败
账目没有改变。Codex是辅助撰写与复核者，这些记录不是独立领域专家或客户批准。

## 扩展范围与使用方法

|范围|题数 / 本轮已执行|扩展内容|需补充的典型证据|
|---|---|---|---|
|安全与隐私|6 / 3|依据标准、整体覆盖、审计机构|项目边界、最近三次审计与机构选择记录|
|基础设施|6 / 3|渗透测试范围、内网加密、日志保留|测试报告范围、链路实施、实际日志与监控记录|
|Web应用|6 / 1|角色、SSO、密码、会话、发布安全检查|客户套餐/配置、IdP规则、发布与培训执行记录|
|物理与数据中心|6 / 1|供应商、认证、物理日志、故障切换、远程设备|供应商证明、机房证书、进出日志、切换演练和设备覆盖率|

打开 `expansion-ready.xlsx`，四个工作表均可按问题列B、答复列C、数据行2—7导入。
答复列留空。题号、译文、条件和来源映射位于问题列外；这项整理通过了产品真实
`inspect_workbook` 预览，每表均识别6题，但没有再次调用模型验证质量改善。
本轮8题仍使用原来冻结的输入副本，不能用新版替换历史观察。

后续从16个未执行问题中选择同一业务范围，先确认原始显示条件与被评估产品，再
选取相应来源并冻结有限批次及判定准则。公开材料不足的用例仍可用于验证保留未知，
但不能虚构审计机构、渗透测试公司、客户配置或机房日志。材料扩展覆盖四类主题，
供应商仍只有 GitLab，尚未建立多供应商、跨行业的效果结论。

## 保存、导出、账目与恢复核验

执行环境为 rc51，应用提交 `3b44a56778841d5c7b5da04abec0f85d1faf1d91`，
数据库版本 `20261010_0037`，主模型 gpt-5.6-sol/v24，回退 grok-4.7/v15。
新建且保留的标注租户为 `1962ce98-b62b-494a-95e3-bf0f2f7e5946`，问卷为
`448ca43d-ff55-49e6-b983-6f6b618605e6`。该轮没有改模型、提示词、预算、代码或部署。

- 首次控制器健康请求返回403，产品操作尚未开始。单独的只读核验确认租户、问卷、
  尝试、调用和作业均为零；原失败回执保留。v2使用既有健康检查 User-Agent，
  重新核验部署身份后执行，不覆盖或盲目重放不确定写入。
- 生成、证据浏览、复核、独立重读四阶段均有单独成功回执，且检查部署模板、凭据
  绑定、数据库版本与模型策略。令牌均已撤销，无残留活动作业。
- 新客户端重新读取的问卷完全相同；再次导出的 XLSX/CSV 字节相同。原始工作簿中
  非答复单元格值不变，目标工作表之外9个 ZIP 成员字节保持一致。
- 8条原始生成尝试、3条模型草稿保持不变；5条补写有身份记录，8条均有单独复核。
  其他租户五类售前数据表的行数与内容哈希在各阶段前后相同。
- 生成调用已知用量284,213 tokens；3次超时调用缺失用量，未按零计。另有59次
  embedding调用（文档11、查询48），全部返回，用量21,179 tokens。复核与重读阶段
  没有新增生成或 embedding调用。没有实际账单，不报告货币成本或节省工时。
- 当前 UTC 日产品生成分发数18（本轮15，此前3）。准入另保守计入77次历史/直接
  请求预留；它们不是本轮实际调用。16次生成上限未突破。
- 扩展工作簿通过真实导入检查、原文/选项/空答复/来源映射核验及四张内容预览。
  本机 Excel COM 无法打开文件，Edge预览也未生成截图，因此不声称已核验原生
  Excel视觉布局；最终包已完成 ZIP 完整性与文件哈希核验。

恢复与证据沿用
[既有清单](D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness/manifest.json)
的 `public_source_trial_20261011` 段，不创建新的恢复组。三份既有任务文档的编辑前
状态由 Git 提交 `41f9c1ca32426978c7fc3822a4f6b6922e1e8949` 完整覆盖，新报告原本不存在。
正式仓库改动限于 PRD、design、implement 与本报告；没有产品契约变化，spec审查
结论是把本轮事实和输入设计教训保存在报告及任务设计中，不改写现有可执行契约。

已删除本轮创建且确认不再使用的临时预览目录
`C:\Users\zhang\AppData\Local\Temp\public-vsaq-preview-lapxfvpq`（239个文件，7,099,490字节）。
原始资料、预览证据、线上标注数据、失败记录和集中恢复点均保留；历史与归属不明
文件未删除。仓库2,993条无关状态记录应保持原基线哈希
`af4f34fc3084af8f1a015cd78a40f6f141b040b84e16ec3ded0db4b544524f25`，
最终范围检查与提交回执记录在集中清单中。更广泛的模型质量及客户验收仍未完成。
