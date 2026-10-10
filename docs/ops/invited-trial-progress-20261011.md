# 四步推进与受邀试用准备（2026-10-11）

本轮完成了安全诊断细分、4题开发观察、12题留出产品流程、浏览器和账本核对，并备齐第二供应商试用材料。用户确认目前没有参与者，因此没有创建真实参与者账户或发送邀请。模型可靠性、独立用户价值和完整商业验收仍未达标。

## 结果与边界

| 步骤 | 本轮结果 | 尚未证明 |
| --- | --- | --- |
| 可靠性处置 | 细分问题覆盖、规则覆盖、答复依据、语言、结果投影和状态一致性六类拒绝原因；题号、译文、分支条件移出问题列；4题开发观察均有草稿 | 新诊断代码尚未部署；传输失败、检索遗漏和冗长拆题尚未解决 |
| 扩展验收 | 12题只执行一次产品尝试，11题有原始草稿，1题失败；随后1题人工补写、12题署名复核并导出 | 11/12是本批草稿可用数量，不是语义准确率；至少3份草稿需要实质补充或修正 |
| 浏览器及运行 | 桌面1440px和手机390px导入、复核、人工填写、引用更正、刷新、XLSX/CSV通过；独立PostgreSQL测试库已清理；现网5个工作负载就绪、账本核对通过 | 浏览器自动化使用本地真实API/DB和受控模型；现网有限顺序流程不等于完整并发容量、告警实收或整机恢复验收 |
| 试用准备 | GitHub Enterprise Cloud第二供应商包：6道原题、3份官方资料、来源与许可、操作说明、复核参考、空白反馈表 | 参与者为0，无独立UAT、人工节省时间或付费意愿数据；两个供应商仍同属开发者SaaS |

## 冻结输入及执行

延续原任务和恢复组，不重复使用原8题的失败样本。此前未执行的16题冻结为4题开发、12题留出，成员、题目、选项、原分支条件、资料哈希和独立复核标准先于推理保存。参考标准未发送给模型。

开发题为 `secprog_standard_name`、`testing_pentests_scope`、`application_authentication_roles_description`、`dc_acclogs`。使用原始问题及选项、限定公开资料摘录，通过实际网关各调用主路一次；无检索、业务持久化、fallback或embedding。4/4可解析，耗时9.969–15.094秒，已知46,069 tokens。核心标准名称和资料缺口判断可用，角色套餐适用性和分支说明仍需人工补充。这只是摘录诊断，不能替代产品验收。

没有进行提示词调优或条件性开发回归。12题通过现网rc51 / schema0037 / v24，维持wuye `gpt-5.6-sol` low streaming120秒、Grok备用180秒、每行360秒、最多2次派发、日200上限。4次直接开发调用加入原77次保守保留；本轮实用17次生成调用（开发4、产品13），未使用40次上限中的剩余额度。

产品资料为9份此前冻结的GitLab官方文档，组合为5个可适用来源，共102,674字节、98块。新租户 `0932eeb5-26c5-4e35-9273-5288b1e9580f` 只用于有标记的公开资料验收，原8题及其他租户历史哈希不变。12题工作簿保留原文、原选项及外置元信息；原自营机房分支作为适用性/资料缺口用例，不能声称GitLab.com自营机房。

## 原始质量观察

11份草稿全部出自主路。`dc_certifications`主路拒绝为`stream_contract`，备用为`draft_contract`，原失败保持；没有重试生成或用人工稿覆盖失败统计。主路该次usage未知，备用已知usage保留。

至少三项需要实质修订：

- `secprog_allinscope`：原稿称资料未明确证明范围受限，但完整上传的ISMS文档明确限定生产资源范围。检索结果没有提供这一关键段落，且PCI DSS背景示例被转成额外补料项。
- `application_custom_auth_pwdreq`：原稿称没有具体密码长度；完整资料已有8/128字符要求，通过产品原文查询找到后补入复核。
- `office_home_device_mgmt`：原稿把集中管理额外限定为“单一中央平台”，复核删除该限制，保留对架构、覆盖率和执行记录的合理询问。

其他已观察限制包括说明文字和选项被逐项重复回答、政策内容形成不必要的前提及冗长补料清单。日志题原`conditional`在复核中改为`insufficient_evidence`，明确区分保留标准与实际配置。原文档中已知数值、缺失事实和不适用产品范围保持分离。没有将本轮与上轮3/8的结果作因果或统计对比。

所有原稿、attempt及引用保留。12题由Codex署名复核，最终2题支持公开功能/默认规则说明、10题保留资料缺口；失败题另有人工作者记录。记录修改类型及原/复核字符数，不伪造真实人员编辑分钟、节省时间或独立批准。

## 核对与工程检查

- 13次产品生成调用：12主路、1备用；11次成功业务额度消费、1次失败释放，无悬挂业务预约。
- 产品生成已知262,795 tokens；1次主路失败usage未知。加上开发观察，生成已知合计308,864 tokens，未知部分未填零。
- 74次embedding：14次文档、60次查询，已知27,723 tokens。5笔文档额度消费合计102,674字节。供应商金额未取得，未以tokens推算实扣金额。
- 查询原文、人工补写、复核、刷新和导出不增加生成或embedding调用。初稿及复核稿XLSX/CSV分别留存，独立重新读取/导出检查保留；非答复单元格保持。
- 针对网关/解析器的265项单元检查、8项实际PostgreSQL诊断/问题协议检查、7项实际PostgreSQL人工补写检查通过。失败额度释放及幂等重放保持。
- 全量非integration：3,386 passed、819 deselected、23 subtests passed。Ruff格式759文件、Ruff检查、mypy279生产源文件通过。
- 浏览器4项通过，人工保存/复核回执丢失后的GET恢复、引用历史和导出均覆盖；独立schema `browser_test_80c95c091e4649bd8b1c21339b0d4781` 清理回执保留。截图人工查看未见手机横向溢出。

保留准备阶段失败：旧浏览器入口命中落后的本地默认库后未开始页面测试，改用已有独立schema夹具；初次产品导入控制器把行范围写成`startRow/endRow`，预览拒绝，未创建表或派发模型。更正为`firstRow=2/lastRow=13`并先通过实际解析器验证后复用同租户及5份资料，没有重复上传。证据控制器一处旧回执版本引用在远端执行前被断言阻止并修正。原记录均未删除。

当前健康检查数据库、Redis、对象存储和队列均正常。资源快照约CPU4%、内存1,982Mi（77%），只是当时观测。rc51同镜像v21回退及v24重新上线有原验证回执；历史不兼容的rc50不能用作当前恢复。按此前用户决定，持续备份和进一步灾备仍延期；历史告警定时投递unknown未改成通过，本轮未发送测试邮件。完整容量、独立业务审核和值班/UAT仍属后续门槛。

## 交付与复现

所有私有原始材料、原稿、回执及恢复清单位于原恢复组：

`D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness/`

其`fallback-model-evidence`中：

- `invited-trial-membership-20261011.json`、`invited-trial-criteria-20261011.json`：固定分组和标准。
- `invited-trial-development-*-run-20261011.json`：完整原始开发响应及安全诊断。
- `invited-trial-acceptance-20261011-*-events.jsonl`：产品原始执行、证据、复核及再次读取。
- `invited-trial-acceptance-20261011-reviewed.xlsx/.csv`：复核后的交付。
- `invited-trial-pack-20261011-materials.zip`：可交给未来参与者的第二供应商材料包。
- `invited-trial-closeout-20261011.json`：最终账本、文件哈希、质量检查、工作区及恢复记录。

第二包使用Google VSAQ固定提交`366d670e7e2a544166f74182e1093e6162786eb7`，Apache-2.0。GitHub官方文档为CC BY4.0，3份抓取正文与许可证已核对。原搜索摘要把默认显示三个月误写成90天保留；官方正文明确一般审计事件180天、Git事件7天，包内采用正文。SAML候选页面实际返回“Page not found”，站点映射403，均排除，未将工具`ok=true`当作内容有效。原候选失败回执保留。

研究通过`smart-search doctor --format json`、`smart-search search ... --format json --output ...`以及`smart-search fetch <官方URL> --format json --output ...`保存证据。官方资料入口：

- [组织角色](https://docs.github.com/en/enterprise-cloud@latest/organizations/managing-peoples-access-to-your-organization-with-roles/roles-in-an-organization)
- [密码要求](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-strong-password)
- [企业审计日志](https://docs.github.com/en/enterprise-cloud@latest/admin/monitoring-activity-in-your-enterprise/reviewing-audit-logs-for-your-enterprise/about-the-audit-log-for-your-enterprise)

Windows会话没有可用Artifact Tool运行库，工作簿使用openpyxl后备方案，完成真实导入、结构及内容检查；没有声称原生Excel视觉验收。反馈表留空，管理员开通步骤有说明，但未预造参与者、登录凭据或验收反馈。

下一项工程优先级是让检索保留已上传资料中的关键范围和数值，并减少背景/选项拆解带来的额外补料项。应另冻结候选及回归预算，不能继续利用本轮留出题调优后仍称留出验收。
