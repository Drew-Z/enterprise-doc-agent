# 正文事实候选 v6 与诊断交付

## 最终行为

保留合成试验collector的脱敏连接诊断：固定HTTPX错误类型、响应头/正文阶段、外部中断状态、失败时关闭响应流。没有新增重试、模型调用、异常消息/URL/headers或部分正文记录。正式生成协议和公开草稿不变。

本轮v6提示词真实试验未通过，已恢复到基线 `529fdff6e2dd8f3a9e2c13ad43e8f7c6b9d63f07` 的v5提示词；v6完整提示词、原始请求/响应及逐条审阅保留，准确测试源码已存入原集中恢复组的rejected-candidate快照。PR #8仍Draft，线上v0.1.44无发布操作。

## 固定预算与真实结果

预定H3、H4、H1、H2共最多24次，逐组通过才继续。H3六次完成后失败，未执行后续18次；无重试、重采样或解码重放。

- H3-R1/R2/R6成功，分类、含义和原文引用通过助手自审。
- H3-R3在等待响应头阶段ReadError，未收到正文，语义未验证。不能确定具体网络节点或远端执行状态。
- H3-R4把两个chunk UUID当citationId，拒绝为presales_invalid_citation。
- H3-R5条件两处文字不一致，被拒绝为presales_invalid_model_output；原始前提中仍把验收未知误判成unmet。

首次有效草稿/分类匹配3/6，有效引文3/3，必要锚点3/9。完整响应5次的部分用量42,904 token（输入19,284、输出23,620），包括2次拒绝；另一次未知，完整用量和金额null。耗时27.187/71.821/106.844秒。H4仅冻结，未调用，不能声称新样例通过。

详见[逐条审阅](../../../evaluation/presales_quality_v6.review.json)及[评测文档](../../../docs/ops/presales-quality-evaluation.md#正文事实候选-v6已撤回)。这些是完整短篇合成证据的已知回归试验，不包含公网上传、检索、存储、客户资料或独立专家审定。

## 工程验证

- 新连接类型诊断：先得到缺transportFailure的红测试，再通过；外部取消：先得到误记running的红测试，再通过。部分HTTP200响应读取失败用例验证无部分正文/敏感异常文本留存且关闭流。
- collector/scorer及售前协议61项通过；撤回v6提示词后同61项再次通过。
- 全仓非集成1639 passed、358 deselected；本地包含历史未提交模块，远端CI独立验证准确提交。日志 `semantic-grounding-pytest.txt`。
- Ruff与553文件格式检查通过；Mypy234个源文件（含collector）通过。恢复旧提示词后相关Ruff/格式再次通过。
- 未更改公开协议/数据库/Web，不重复上轮24项数据库、3项浏览器验收；此处不将它们计为本轮执行。
- 最终提交、CI及只读线上核查记录在 `semantic-grounding-final-review.json`。

## 后续与恢复

不要继续单纯堆叠提示说明。下一设计先降低输入中引用标识歧义及重复输出字段，仍需独立核验未知事实判断；可在单独冻结计划下比较现有可用模型。本轮不追加付费调用，不改线上路由、认证、固定4C4G资源、客户记录或额度，不发邮件。

复用集中恢复组 `D:\Agent\codex\backups\tasks\20260921T060311.970Z-saas-public-pilot-integration` 的 `presales-semantic-grounding` 阶段。历史工作区及worktree不清理；本轮测试缓存和PR临时材料按精确清单回收。父任务保持in_progress，不将候选失败记为整个项目完成。
