# 售前响应质量重复评测

公网评测通过已经部署的公开演示 API 完成 TXT 上传、真实解析与检索、外部模型生成和结果读取。它不绕过企业权限，不预置草稿，也不直接用参考答案调用模型。下文另外标明候选代码的生成阶段试验，其范围不包含公网上传、检索和持久化。浏览器交互、人工复核和 CSV 的验收见[公开演示手册](public-pilot-runbook.md#公开演示企业)。

## 固定测试集

输入为 [presales_quality_v1.json](../../evaluation/presales_quality_v1.json)，参考答案单独存放于 [presales_quality_v1.gold.json](../../evaluation/presales_quality_v1.gold.json)。六份资料、六条要求取自 2026-09-12 冻结的合成采购挑战，所有企业、订单与条款均为虚构。原文与参考标注保持不变；gold 用输入文件的 SHA-256 绑定版本。

仓库通过 Git 属性保留输入、原始结果和提示词的原始字节（含换行），避免不同系统自动转换换行后破坏哈希校验。

| 要求 | 重点 | 预期判断 |
|---|---|---|
| C1-R01 | WORM400 已购；400 天保留且管理员不可删 | 支持 |
| C1-R02 | BYOK 与独享 HSM 未购；还需要交接与恢复演练 | 有条件支持 |
| C1-R03 | 四台各 250 GiB，仅 1000 GiB，小于 1 TiB | 不满足 |
| C1-R04 | RPO 保证不能替代缺失的 RTO 保证 | 证据不足 |
| C1-R05 | 明确签认的补充协议只覆盖可用性条款 | 支持 |
| C1-R07 | 境内备份承诺与新加坡副本要求互相矛盾 | 资料冲突 |

## 运行

在仓库根目录使用 PowerShell。`run` 会创建新的演示企业并产生真实模型/嵌入用量；不要放入每次提交的 CI。默认两轮，每轮新建独立会话、最多六次生成，共最多 12 次。`--object-host` 填部署配置批准的签名上传域名，不填完整 URL、密钥或签名链接。

```powershell
& .\.venv\Scripts\python.exe -B -X utf8 -m scripts.evaluate_presales_quality run `
  --base-url https://agent.playlab.eu.cc `
  --object-host YOUR_R2_HOST `
  --repeats 2 `
  --output "$env:TEMP\presales-quality-run.json"
```

输出文件必须不存在，父目录必须已经存在。脚本按请求前后保存进度；发生网络失败只读取已有响应表，不自动再次生成。额度、权限拒绝或尚未结束的请求会终止整轮。cookie、CSRF 和签名上传 URL 只在内存中使用，不写入报告。每轮结束退出登录，演示资源沿用产品自身的到期回收。

离线评分不会产生任何网络或模型调用：

```powershell
& .\.venv\Scripts\python.exe -B -X utf8 -m scripts.evaluate_presales_quality score `
  --run "$env:TEMP\presales-quality-run.json" `
  --output "$env:TEMP\presales-quality-score.json"
```

## 如何解读

- 未完成或失败的题保留在计划总题数内；不只统计成功响应。
- 状态匹配、错误肯定、逐字引文有效性、必要证据覆盖分别计算。引用存在于原文不代表其一定能证明结论。
- 记录全部可观察生成请求的最短、中位数和最长耗时，包含失败。两轮小样本不宣称长期可用率或 p95 服务承诺。
- 记录应用观察到的提供方请求次数和 token。缺失用量为 `null`；没有核实入口价格和账单时，金额也为 `null`。
- 原始草稿不因评分或助手审阅而改写，不把助手审阅标为客户批准。短篇合成测试不代表真实客户准确率、商业价值或独立业务专家审定。

## 本次实测

2026-09-23，公网 v0.1.43、`presales.v1` 提示词与 `grok-4.6` 路由。两个独立演示企业各上传六份原文、执行六条要求，结束后均退出；没有自动重试。

[原始响应与只读核查结果](../../evaluation/presales_quality_v1.baseline.json)、[离线评分](../../evaluation/presales_quality_v1.baseline-score.json)和[助手逐条审阅](../../evaluation/presales_quality_v1.baseline-review.json)分别保留。审阅非盲、未经独立业务专家审定，不修改原始草稿或客户复核状态。

| 项目 | 观察结果 |
|---|---:|
| 计划 / 实际生成 HTTP 请求 | 12 / 12 |
| 持久化有效草稿 | 10 / 12 |
| 状态匹配（包含失败题） | 7 / 12 |
| 状态匹配（仅有草稿的题） | 7 / 10 |
| 错误肯定分类 | 1 |
| 有效原文引文 | 20 / 20 |
| 必要证据锚点覆盖（包含失败题） | 19 / 24 |
| 成功响应最短 / 中位 / 最长 | 56.1 / 74.4 / 107.6 秒 |
| 全部 HTTP 尝试最短 / 中位 / 最长 | 4.5 / 71.4 / 107.6 秒 |
| 应用观察到的提供方请求 | 11 |
| 有完整 token 记录的请求 | 10 |
| 金额 | 未知 |

10 次有记录响应合计 33,570 输入 token、28,080 输出 token、61,650 总 token。这是**部分用量**，不是全部费用；另一次已派发请求在引文校验时失败，应用没有保存其 token。评分中的完整用量合计因此保持 `null`。

| 要求 | 第一轮 | 第二轮 | 结论 |
|---|---|---|---|
| C1-R01 | 支持 | 支持 | 已购选项判断通过 |
| C1-R02 | 不满足 | 有条件支持 | 分类不稳定；第一轮漏引 HSM 未购行 |
| C1-R03 | 不满足 | 不满足 | 容量换算通过 |
| C1-R04 | 有条件支持 | 不满足 | 两轮均未正确识别证据不足 |
| C1-R05 | 支持 | 引文校验失败 | 失败结果未保存为可用草稿 |
| C1-R07 | 资料冲突 | 生成失败 | 第二轮未派发到模型 |

第二轮 C1-R07 的 HTTP 请求在 4.5 秒返回 502，当时结果未定。之后对本轮两个企业执行只读核查，确认该条在服务端约 14.2 秒后失败、提供方请求数为 0；没有再次生成。5 个服务容器均未重启。现有信息不足以把 502 精确归因于 Cloudflare、本地网络或某个上游。

这些结果表明复杂资料判断还不稳定，不能仅凭先前简单演示三条全成功就判定质量过关。当前重点是区分有证据的条件路径、硬限制和缺失证明；同时保留严格原文引文校验。需继续做独立保留集及真实客户资料验证。

## 提示词候选对照与处理决定

同日测试了更明确的 `presales.v2` 判定说明，见[完整候选提示词](../../evaluation/presales_quality_v2.candidate-prompt.txt)和[原始对照记录](../../evaluation/presales_quality_v2.candidate.json)。它使用新演示企业的真实上传资料和产品检索，在单独的只读诊断进程中调用同一模型；没有修改正在运行的 API，也没有把诊断输出保存成产品草稿。

6 次模型调用均收到完整响应，5 条通过引用校验、4 条与参考分类相符。RTO 仍被误判为不满足；SLA 一条因模型将原文末尾的中文句号 `。` 改成英文句点 `.` 而被拒绝。这是**可定位的引文转写错误**，不应通过放宽逐字校验来掩盖。

本轮候选未通过验收，相关运行时代码改动已撤回；候选撤回时线上和主分支产品规则继续使用 `presales.v1`。对照的 6 次响应共报告 42,168 token，模型处理耗时中位数约 73.1 秒，未显示明确的速度改善；它只有一轮，且使用已知问题集，不能直接视为独立测试成绩或与两轮公网请求耗时等价比较。

另一次初步诊断误用了已退出的演示企业，资料被正常权限规则过滤，已停止且不纳入质量成绩。它收到了 3 次模型响应，另 1 次是否已派发无法确认；记录与未知费用均保留。之后改用新企业，并在模型派发前检查证据非空。所有测试会话已退出。

## 受控引用改进与新资料验证

`presales.v3` 随 [v0.1.44 部署](https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/35853410044)上线：“模型选择本次提供的引用编号、服务端返回对应原文”，减少标点和转写错误。每段最长 600 字符，长文本沿原文顺序分段；未知、重复、跨请求编号和夹带改写引文的输出会被拒绝。原有企业/版本校验、生成后再次授权、复核与 CSV 格式保留，旧草稿不会被改写。线上模块哈希与发布提交 `9a65a72` 一致，原企业业务记录保留；模型路由、超时与服务器配置未变。

分类规则没有采用上次被拒绝的 v2 候选。实测使用新冻结的 [H1 资料](../../evaluation/presales_quality_holdout_v1.json)和[独立存放的参考标注](../../evaluation/presales_quality_holdout_v1.gold.json)，覆盖已购归档、未购单点登录、报文上限、平均值与 P99、可用性优先级及删除期限冲突。资料和标注在首次请求前冻结；均由助手编写并自审，不能称为独立专家盲评。原 C1 基线、失败和用量记录保持不变。

2026-09-23，新建独立演示企业上传六份 TXT，通过公开 API 生成六次，全部 HTTP 200 并保存草稿；没有自动重试，演示额度为 6/6，结束后已退出。保存[原始响应](../../evaluation/presales_quality_holdout_v1.v0144.json)、[离线评分](../../evaluation/presales_quality_holdout_v1.v0144.score.json)及[助手逐条审阅](../../evaluation/presales_quality_holdout_v1.v0144.review.json)，原草稿没有修改，也没有被标记为客户复核通过。

| 项目 | H1 单轮观察结果 |
|---|---:|
| 计划 / 实际生成 / 有效草稿 | 6 / 6 / 6 |
| 状态匹配 | 5 / 6 |
| 错误肯定分类 | 1 |
| 有效原文引文 | 11 / 11 |
| 必要证据锚点覆盖 | 10 / 11 |
| 最短 / 中位 / 最长 HTTP 耗时 | 56.9 / 65.4 / 94.5 秒 |
| 应用观察到的提供方请求 / 有 token 记录 | 6 / 6 |
| 输入 / 输出 / 总 token | 18,733 / 16,931 / 35,664 |
| 金额 | 未知 |

| 要求 | 参考分类 | 实际分类 | 审阅结果 |
|---|---|---|---|
| H1-R1 已购 A360 | 支持 | 支持 | 引用覆盖已购数量与保留期；答案对条款出处的归因可更精确 |
| H1-R2 未购 SSO-BIZ | 有条件支持 | 支持 | 错误肯定，采购、域名验证和联调条件均未列出 |
| H1-R3 单次报文上限 | 不满足 | 不满足 | 正确识别 16 MiB 硬上限，未虚构提升方案 |
| H1-R4 平均值与 P99 | 证据不足 | 证据不足 | 正确要求补充 P99 测试或承诺 |
| H1-R5 可用性优先级 | 支持 | 支持 | 99.97% 与限定优先级均有逐字引用 |
| H1-R6 删除期限冲突 | 资料冲突 | 资料冲突 | 保留冲突双方；中文问题返回英文，且未提出明确澄清问题 |

H1-R2 的引文自身包含全部前置条件，答案仍遗漏它们，说明**逐字引用正确不能保证判断正确**。本次未出现引用校验失败，只能支持这六次调用的观察结果；不能据此保证此后零失败，也不能与不同资料的 C1 两轮结果直接比较准确率或速度。优先继续处理采购范围与前提条件、答案语言和交付措辞，随后需要新的独立审定资料验证。

本次六条售前请求的 token 均有记录，但入口价格及账单未核实，金额保持 `null`。标准部署另有 Agent、Embedding 与治理检查，其用量不包含在上述六次统计内；标准 smoke 未测量模型成本。

复现 H1 时显式选择输入与 gold，不使用默认的 C1 参数：

```powershell
& .\.venv\Scripts\python.exe -B -X utf8 -m scripts.evaluate_presales_quality run `
  --input evaluation/presales_quality_holdout_v1.json `
  --base-url https://agent.playlab.eu.cc --object-host YOUR_R2_HOST `
  --repeats 1 --output "$env:TEMP\presales-h1-run.json"

& .\.venv\Scripts\python.exe -B -X utf8 -m scripts.evaluate_presales_quality score `
  --input evaluation/presales_quality_holdout_v1.json `
  --gold evaluation/presales_quality_holdout_v1.gold.json `
  --run "$env:TEMP\presales-h1-run.json" --output "$env:TEMP\presales-h1-score.json"
```

## 前置条件候选 v4（未发布）

`presales.v4` 增加带引用的前置条件及 met/unmet/unknown 状态，校验未满足条件与支持结论的一致性，并拒绝全文非中文的生成正文。该语言检查只检查汉字是否存在，允许产品名、技术词和英文原文引用；它不等于完整语言识别。公开草稿、历史数据、人工复核与 CSV 契约不变。

使用原 H1 作为**已知回归集**，另冻结 [H2 输入](../../evaluation/presales_quality_holdout_v2.json)及 [H2 参考标注](../../evaluation/presales_quality_holdout_v2.gold.json)。H2 覆盖已完成、未完成、状态未知的启用条件，以及导出硬上限、报告缺失和服务窗口冲突。全部由助手编写和自审，不是独立专家审定。

这次从本地候选代码调用同一 `grok-4.6` 售前路由，直接提供完整的短篇合成证据，每题一次、共 12 次，不涉及上传、检索、企业数据、额度或公网持久化。不能把该结果当作公网端到端准确率，也不能与上次 HTTP 耗时直接比较。

| 项目 | H1 已知回归 | H2 新合成样例 |
|---|---:|---:|
| 实际模型请求，无重试 | 6 | 6 |
| 初始候选接受的草稿 | 5 | 5 |
| 修正解码后原始响应重放成功 | 6 | 6 |
| 重放草稿分类匹配 | 6/6 | 4/6 |
| 错误肯定分类 | 0 | 0 |
| 必要原文证据覆盖 | 11/11 | 10/10 |
| 原文引用数量（均逐字有效） | 12 | 11 |
| 全部请求最短 / 中位 / 最长（秒） | 42.844 / 60.430 / 82.563 | 53.968 / 64.875 / 81.250 |
| 输入 / 输出 / 总 token | 22,399 / 24,093 / 46,492 | 19,161 / 24,041 / 43,202 |

初始 H1-R5、H2-R3 的模型在前置条件中明确选择了有效引用，但未在顶层再次重复，初始适配器因重复要求而拒绝。修正后对所有原始响应重放同一解码器，按顺序合并两个位置的明确选择，继续检查请求内编号、准确原文和最终条数；没有改写分类、补造引文或再次调用模型。**初始 10/12 与重放 12/12 分别保留**，重放不算新真实调用。

原始响应：[H1](../../evaluation/presales_quality_holdout_v1.v4-gateway.json)、[H2](../../evaluation/presales_quality_holdout_v2.v4-gateway.json)；解码重放及评分：[H1](../../evaluation/presales_quality_holdout_v1.v4-gateway.replay.json)、[H2](../../evaluation/presales_quality_holdout_v2.v4-gateway.replay.json)；[逐项审阅](../../evaluation/presales_quality_v4.review.json)。费用金额未知：提供方返回了计价刻度字段，但单位与账单未经核实，不能据此报告金额。

H1 的采购、域名验证、联调条件现在完整保留，冲突回答也使用中文并提出澄清问题。然而 H2-R5 把“未提供 SOC 2 Type II 报告”判为不满足，而证据明确不证明公司是否持有报告；H2-R6 看到了冲突双方仍判为不满足，未保留资料冲突状态和澄清问题。H2-R2 的条件采用“已购买 / 已完成”的表达，也应改为明确待办措辞，避免被读成已完成事实。

**处理决定：维持草案，不合并、不部署。** 下一步需要解决“缺少证明”和“证据相互矛盾”的判定顺序与交付措辞，再使用新的冻结资料验证；不修改 H2 标注或反复重跑同题挑选成功结果。线上继续 v0.1.44。

复现单次生成（会产生真实模型用量；输出路径必须尚不存在）：

```powershell
& .\.venv\Scripts\python.exe -B -X utf8 -m scripts.evaluate_presales_gateway `
  --input evaluation/presales_quality_holdout_v2.json `
  --provider-env D:\path\to\provider.local.env `
  --output "$env:TEMP\presales-h2-gateway-new-run.json"
```

## 判定顺序候选 v5（未发布）

`presales.v5` 继续使用 v4 的前提状态与受控原文引用，明确区分未消解的同范围资料冲突、直接反证、缺少证明、有条件启用和全部满足。仅有“未附证书”不能推出“未认证”；强制禁止条款也不能自动覆盖同范围、互不优先的另一份有效承诺。未满足或未知的前提应写为待办，冲突草稿必须包含澄清问题。这些结构检查不能保证正文推理正确。

本轮先冻结[完整提示词](../../evaluation/presales_quality_v5.candidate-prompt.txt)、[H3 输入](../../evaluation/presales_quality_holdout_v3.json)和[独立存放的 gold](../../evaluation/presales_quality_holdout_v3.gold.json)，再执行 H1/H2 已知回归和 H3 新合成样例各六次。H3 由助手编写并自审，未经独立专家审定。仍从本地候选通过同一 `grok-4.6` 路由提供完整短篇证据，不经过上传、检索、企业持久化或演示额度；没有重试、重新抽样或响应重放，也没有在看见结果后修改提示词、输入或 gold。

原始结果与只计首次结果的离线评分分别保存：[H1 原始](../../evaluation/presales_quality_holdout_v1.v5-gateway.json) / [评分](../../evaluation/presales_quality_holdout_v1.v5-gateway.score.json)、[H2 原始](../../evaluation/presales_quality_holdout_v2.v5-gateway.json) / [评分](../../evaluation/presales_quality_holdout_v2.v5-gateway.score.json)、[H3 原始](../../evaluation/presales_quality_holdout_v3.v5-gateway.json) / [评分](../../evaluation/presales_quality_holdout_v3.v5-gateway.score.json)。[逐条语义审阅](../../evaluation/presales_quality_v5.review.json)与机械分类分数分开，不修改原草稿，也不代表客户复核通过。历史 v4 失败与重放证据保留。

| 项目 | H1 已知回归 | H2 已知回归 | H3 新合成样例 | 合计 |
|---|---:|---:|---:|---:|
| 计划 / 实际模型请求 | 6 / 6 | 6 / 6 | 6 / 6 | 18 / 18 |
| 首次接受的中文草稿 | 5 / 6 | 4 / 6 | 6 / 6 | 15 / 18 |
| 分类匹配（包含失败题） | 5 / 6 | 4 / 6 | 6 / 6 | 15 / 18 |
| 分类匹配（仅有草稿） | 5 / 5 | 4 / 4 | 6 / 6 | 15 / 15 |
| 错误肯定分类 | 0 | 0 | 0 | 0 |
| 有效逐字引文 | 9 / 9 | 9 / 9 | 11 / 11 | 29 / 29 |
| 必要证据锚点覆盖 | 8 / 11 | 8 / 10 | 9 / 9 | 25 / 30 |
| 全部请求最短 / 中位 / 最长（秒） | 32.406 / 57.274 / 120.000 | 12.766 / 67.711 / 107.781 | 54.656 / 92.601 / 108.844 | 12.766 / 69.438 / 120.000 |

错误肯定指标只比较分类标签，不能据此断言正文无错误。包含失败题的首次草稿率及分类匹配率均为 83.3%；这18次短篇合成调用不能代表服务长期可用率、客户准确率或速度改善。

| 观察项 | 原始结果与处理 |
|---|---|
| H1-R1 | 120 秒超时，无响应正文和用量；保留失败，不重试。 |
| H2-R4、H2-R5 | 分别在 12.766、21.312 秒连接失败，无 HTTP 状态、正文或用量。不能据此确认 H2-R5 的缺报告误判已修复，也不能确定故障发生在哪一层。 |
| H1-R2 | 三项采购、域名验证和联调前提均保留为待办；但只引用 SSO 规格，未引用冻结 gold 要求的订单已购数量 0。规格也写明未购买，仍不据此修改 gold 或隐去缺口。 |
| H1-R4 | 证据不足分类正确，但答案过于简略，并询问引用原文已明确回答的“该 SLA 是否规定 P99”。应说明平均值不能证明 P99，索取真正缺失的测试或承诺。 |
| H2-R2、H2-R6 | OCR 条件改为“需购买 / 需完成”；服务窗口由 v4 的“不满足”改为“资料冲突”，保留双方引用并提出澄清。 |
| H3-R1、H3-R2 | 正确区别缺少 ISO 证书与明确尚未通过 PCI 评估。 |
| H3-R3 | 虽分类及冲突双方引用正确，正文却说“本要求与 H3-BASE 条款直接矛盾”。实际要求与 BASE 的德国独占条款一致，与 ADD 的新加坡副本条款冲突，正文关系写反。 |
| H3-R5 | 已购模块没有重复采购待办，但把“恢复验收状态未登记”标为 `unmet` 并写成尚未满足；应保留 `unknown`，请求确认是否已完成。 |

缺失的五处必要锚点中，四处来自无草稿的 H1-R1 和 H2-R4，一处来自 H1-R2。H2-R5 的 gold 没有强制引用锚点，其失败仍计入六题分母。

15 次有完整记录的响应合计 **55,938 输入、63,242 输出、119,180 总 token，属于部分用量**。三次失败用量未知，因此完整合计保持 `null`；单位价格与账单未核实，金额也为 `null`，不能将失败视为免费。H3 六次都有记录，合计 21,470 输入、29,154 输出、50,624 总 token。

**处理决定：PR #8 继续为草案，不合并、不部署。** 冻结门槛要求18/18首次合法中文草稿、分类匹配、必要证据及前提完整，并通过逐条语义审阅；本轮因无草稿、证据缺口及正文/状态错误均未达标。线上 v0.1.44 的五个服务已只读核查就绪，镜像未变。本轮没有执行候选的公网生成/复核/导出验收。

下一轮应先针对条款关系写反、未知状态与未满足混同，以及已知事实重复补问建立可复查的反例；之后冻结新候选和有界验证计划，再进行真实调用。当前18次结果封存，不继续追加同题请求挑选成功答案。连接失败与语义错误分别排查，不自动增加重试或更改模型路由。

只评分现有原始报告的命令不会调用模型（输出路径必须尚不存在）：

```powershell
& .\.venv\Scripts\python.exe -B -X utf8 -m scripts.score_presales_gateway `
  --input evaluation/presales_quality_holdout_v3.json `
  --gold evaluation/presales_quality_holdout_v3.gold.json `
  --run evaluation/presales_quality_holdout_v3.v5-gateway.json `
  --output "$env:TEMP\presales-h3-v5-original-score.json"
```

## 正文事实候选 v6（已撤回）

v6 在相同模型、路由、时限和输出结构下，补充正文事实关系、unknown/unmet 区别、避免重复补问及订单出处说明。试验前冻结[完整提示词](../../evaluation/presales_quality_v6.candidate-prompt.txt)、[H4 新输入](../../evaluation/presales_quality_holdout_v4.json)与[单独存放的参考标注](../../evaluation/presales_quality_holdout_v4.gold.json)。预定顺序为 H3、H4、H1、H2，各六次、最多24次；每组先通过首次草稿、分类、引用与语义审阅，才能继续下一组。

**首组 H3 未通过，实际仅执行六次，其余18题未执行。** [原始结果](../../evaluation/presales_quality_holdout_v3.v6-gateway.json)、[首次结果评分](../../evaluation/presales_quality_holdout_v3.v6-gateway.score.json)及[逐条审阅](../../evaluation/presales_quality_v6.review.json)保留，没有重试、重放或改写失败。H3 是已知回归集；H4 尚未调用模型，不能提供新样例成绩。这些仍是完整短篇合成证据的生成阶段试验，非公网端到端或独立专家评测。

| 项目 | H3 单次试验 |
|---|---:|
| 实际请求 / 收到完整 HTTP 200 正文 | 6 / 5 |
| 首次有效草稿 / 分类匹配（包括失败） | 3 / 6 |
| 仅有效草稿的分类匹配 | 3 / 3 |
| 有效逐字引文 / 必要锚点 | 3 / 3、3 / 9 |
| 全部请求最短 / 中位 / 最长 | 27.187 / 71.821 / 106.844 秒 |
| 未执行的后续题 | 18 |

| 题目 | 观察结果 |
|---|---|
| H3-R1、R2、R6 | 有效中文草稿；分别正确区分缺少认证证明、明确尚未评估和附件硬上限。 |
| H3-R3 | 27.187秒后发生 `ReadError`，阶段为 `awaiting_response_headers`。无响应状态、正文或用量，不能确认条款关系问题是否修复。 |
| H3-R4 | HTTP 200，但模型将两个证据中的 chunk UUID 填入 `citationId`，不在本次引用目录，按原规则拒绝。原始标签为 supported 不等于有可用草稿。 |
| H3-R5 | HTTP 200，但 conditions 的“需配置/需完成”与前提 condition 的“配置/完成”未逐字相同，触发既有一致性检查；还把验收状态未登记标为 unmet，说明原语义问题仍在。 |

新增的试验诊断只记录 HTTPX 白名单错误类型和响应头/正文阶段，排除异常消息、URL、headers、凭据和部分正文。中断的试验保存为 interrupted；已取得响应的流会关闭。它可以区分客户端观察到的故障阶段，**不能确定哪一网络节点故障、上游是否执行完成或是否计费**。

五次完整响应报告 19,284 输入、23,620 输出、42,904 总 token，为**部分用量**，包含两条被拒绝的输出；连接失败的一次用量未知，完整合计和金额均为 `null`。一次短篇非独立试验不能证明某个提示词或模型整体更差，也不能以提供方的 max_tokens 参数推断真实计费上限。

**处理决定：撤回本轮 v6 提示词，保留 v5 草案与线上 v0.1.44。** 脱敏诊断、测试、新资料和失败证据保留。源码不接受 UUID 作为引用别名、不忽略未知状态错误，也不把非法草稿修补后计作成功。尚未完成原定24题门槛，因此没有发布或候选公网验收。

后续应先减少模型输入中无关的内部标识，并研究由结构化前提直接形成展示条件，避免让模型重复抄写同一字段；仍需单独验证事实判断。若比较其他已配置模型，应冻结模型、样例、费用上限与停止规则后执行，不延长本轮试验或更改正在运行的模型路由。

## 单一来源协议候选 v7（未发布）

v7 实施了上述接口调整。模型输入只保留本次 `citationId`、准确原文、来源显示标签、文件名、版本及适用范围、页码/标题；内部 chunk/version/document/generation UUID 与 hash 不再发送。模型仅生成结构化 `prerequisites`，服务端将 unmet/unknown 条件按原顺序去重形成公开 `conditions`，met 不列待办。公开草稿、数据库、人工复核和 CSV 不变；未知或跨请求编号仍严格拒绝，没有引用别名、修补、改判或重试。

新 `presales-gateway-run-v2` 分别记录真实模型输入和内部合成证据。评分核对两者的逐片段绑定及已接受结果；旧 v1 原始报告仍可评分。测试覆盖篡改来源、范围、片段、编号、条件和原始输出的拒绝，失败记录不能通过重解码变成成功。

冻结[完整提示词](../../evaluation/presales_quality_v7.candidate-prompt.txt)、代码和四份数据后，沿用 H3 → H4 → H1 → H2 的24次上限，每组六题审阅后才继续。**H3有两次超时，实际执行六次，其余18次未执行，H4仍未调用模型。** 未修改输入/gold或模型路由。保留[全部原始结果](../../evaluation/presales_quality_holdout_v3.v7-gateway.json)、[原始结果评分](../../evaluation/presales_quality_holdout_v3.v7-gateway.score.json)和[逐条审阅](../../evaluation/presales_quality_v7.review.json)。

| 项目 | H3 单次试验 |
|---|---:|
| 实际请求 / 完整 HTTP 200 正文 | 6 / 4 |
| 首次有效草稿 / 分类匹配（包括失败） | 4 / 6 |
| 已接受草稿分类及语义审阅通过 | 4 / 4 |
| 有效逐字引文 / 必要锚点 | 5 / 5、4 / 9 |
| 全部请求最短 / 中位 / 最长 | 42.828 / 77.789 / 120 秒 |
| 后续未执行题 | 18 |

| 题目 | 观察结果 |
|---|---|
| H3-R1、R2 | 正确区分未附 ISO 证书与明确尚未通过 PCI 评估；没有把材料缺失写成无资质。 |
| H3-R3 | 120秒时在等待响应头阶段取消；没有正文，冲突双方关系是否正确仍无法判断。 |
| H3-R4 | 正确采用仅对吞吐生效的优先条款，300 ≥ 250；同时引用订单及补充协议，未再填写 chunk UUID。 |
| H3-R5 | 120秒时在等待响应头阶段取消；无法验证验收 unknown 状态及真实前提投影效果。 |
| H3-R6 | 正确说明单张12个附件的硬上限小于20，未编造提高上限的途径。 |

四份完整响应没有出现引用编号或重复条件协议错误；这是小样本观察，不能推断稳定成功率。两次超时没有响应状态、正文或用量，客户端阶段记录不能确定网络节点、供应商执行/计费状态或根因。四份响应的**部分用量**为10,133输入、13,155输出、23,288总token；其中提供方报告12,767个reasoning token，不能据此解释没有正文的两次调用。一次完成响应报告5,012输出token，仍说明请求的 `max_tokens=4000` 不能视为该路由的账单硬上限。全部用量及已核验金额保持 `null`。

本地验证包括1,653项非集成测试、25项真实PostgreSQL检查、3项售前浏览器和3项真实入库浏览器检查，以及Ruff和Mypy。浏览器模型是受控模拟，覆盖批量/文件夹恢复、TXT/PDF/DOCX、草稿/复核导出、刷新及隔离，不能替代上面的真实模型质量结果。

**保留 v7 为 Draft 候选，线上维持 v0.1.44。** 下一步重点是查明所选模型路由的等待时间与输出预算兼容性，再另行冻结有界验证；不追加本轮付费尝试，不通过延长超时、放宽校验或忽略失败取得发布资格。原来两个关键语义案例仍未完成验证。

## 指定新渠道验证

2026-09-24（北京时间），用户更新配置后，使用明确选定的 primary 路由 `https://windhub.cc/v1` / `grok-4.7`，保持 v7 提示词、协议、H3资料和 gold 不变。`PROVIDER_NAME` 的旧标签不参与路由选择；未改写用户凭据文件。配置及返回模型名仅为渠道标识，未独立核验底层模型身份。

collector 支持 `--model-route primary|fallback`，缺省仍为 fallback。每题一次、120秒上限、无自动切换或重试。独立保存[原始结果](../../evaluation/presales_quality_holdout_v3.v7-windhub-gateway.json)、[离线评分](../../evaluation/presales_quality_holdout_v3.v7-windhub-gateway.score.json)和[逐条语义审阅](../../evaluation/presales_quality_v7.windhub.review.json)，不覆盖旧 Grok 4.6 记录。此前误选的 GLM 诊断在用户澄清时已停止，五次已记录超时及一条中止中的未知结果单独保留，不计入新渠道六次试验。

| 项目 | 新渠道 H3 结果 |
|---|---:|
| 实际请求 / HTTP 200 | 6 / 4 |
| 有效草稿 / 分类匹配（包括失败） | 3 / 6 |
| 语义审阅通过 / 已接受草稿 | 2 / 3 |
| 准确原文引用 / 必要证据覆盖 | 6 / 6、6 / 9 |
| 最短 / 中位 / 最长耗时 | 9.234 / 23.852 / 120.016 秒 |
| 后续未执行题 | 18 |

| 题目 | 观察结果 |
|---|---|
| H3-R1、R3 | 120秒等待响应头超时；无草稿、用量或可验证的语义。 |
| H3-R2 | 23.453秒收到HTTP200，但标准成功字段为空、无可读取的choices，应用记录为 `presales_invalid_model_output`。用户渠道日志截图对应 `upstream_error`；不能将其归因于模型正文JSON或引用错误。原始错误正文未保留，确切错误结构未知。 |
| H3-R4 | 正确采用仅对吞吐生效的优先关系，300≥250；三个来源引用准确。 |
| H3-R5 | 正确判断conditional，已购met未列待办、策略配置unmet正确；但把“恢复验收状态未登记”标为unmet而非unknown，并要求完成验收。分类和引用通过，语义不通过。 |
| H3-R6 | 正确指出单张12个附件的硬上限不能满足20个附件，没有虚构升级或拆单方案。 |

用户截图按顺序、耗时及三条成功记录的精确token数与本次请求吻合：前三条为两次 `do_request_failed`、一次 `upstream_error`，后三条为消费记录。这是用户提供的截图关联，非独立后台查询；截图含网络信息，未复制或发布到仓库。页面三条消费合计显示 `0.061498`，失败条目显示0，但币种及最终账单未核实，不据此补齐未知用量或费用。应用已知部分用量为11,084输入、7,419输出、18,503总token；完整用量和已核验金额仍为null。

一次只读 `/models` 在1.828秒返回200且列出目标模型，只证明目录可读。新渠道部分完成请求更快，但本轮小样本同时暴露渠道故障及unknown状态判断错误，不能推断稳定速度或成功率。H3未通过，H4/H1/H2的18次调用均未执行，线上保持v0.1.44。

后续产品可靠性应单独验收“一次用户操作经有界恢复后能否完成”，包括后台任务、恢复进度、有限切换和费用边界；原始单次渠道失败继续保留。自动恢复不替代语义与引用质量验收。本轮未实现或上线自动重试，也未追加模型请求。

## 逐项前提评分与 v8 候选（2026-09-25）

新增离线评分器将每个业务前提的期望状态、对应输出及原文证据一起核对。[H3 前提参考](../../evaluation/presales_quality_holdout_v3.prerequisites.json) 是看过 v7 失败后的回归补充，不是新盲测，原 H3 输入和分类 gold 保持原字节。映射由审核者明确填写，不按条件关键词或数组顺序猜测。

对原 Windhub v7 运行新增[逐项映射](../../evaluation/presales_quality_v7.windhub.prerequisite-review.json)及[离线结果](../../evaluation/presales_quality_v7.windhub.prerequisite-score.json)：H3-R5 分类正确，三项状态中两项正确、验收一项将 unknown 判为 unmet，因此该题不通过。H3-R1/R2/R3 的原调用失败仍保留，只有 R4/R6 通过本项检查，共 2/6；没有新模型请求，也没有重写旧运行或旧评分。这里的通过仅指前提检查，不是完整语义或独立领域验收。

复现命令（输出必须是尚不存在的新文件；本失败样例保存报告后返回 exit 1）：

```powershell
& .\.venv\Scripts\python.exe -B -X utf8 -m scripts.score_presales_prerequisites `
  --input evaluation/presales_quality_holdout_v3.json `
  --gold evaluation/presales_quality_holdout_v3.gold.json `
  --run evaluation/presales_quality_holdout_v3.v7-windhub-gateway.json `
  --expectations evaluation/presales_quality_holdout_v3.prerequisites.json `
  --review evaluation/presales_quality_v7.windhub.prerequisite-review.json `
  --output "$env:TEMP\presales-prerequisite-score-new.json"
```

[v8 完整候选提示词](../../evaluation/presales_quality_v8.candidate-prompt.txt) 先评估事实状态，再生成待办与总分类，并区分业务事件、是否通过及报告登记。unknown 先请求确认状态，不能直接断言尚未完成。模型 schema 展示顺序也先列 prerequisites/state；没有新增协议字段、放宽引用校验、改判或重试。文字与顺序调整的效果仍须由真实输出验证。

[新试验计划](../../.trellis/tasks/09-24-commercial-operations-acceptance/v8-trial-plan.json) 冻结提示词/数据/两份参考的 SHA：现有 primary `windhub.cc` / `grok-4.7`，最多六次，每题一次、120 秒、请求 max_tokens=4000，零重试、零切换，不执行 H4/H1/H2。用户收到具体批次及金额未知的提问后确认继续，已按提交 `4368be04f2db8905e51da089433cb72874385ee1` 执行。

| 项目 | v8 H3 单次回归 |
|---|---:|
| 实际请求 / 有效草稿 / 分类匹配 | 6 / 6 / 6 |
| 准确原文引用 / 必要锚点 | 11/11、9/9 |
| 逐项前提检查 / 助手正文审阅 | 6/6、6/6 |
| 最短 / 中位 / 最长耗时 | 10.156 / 40.258 / 111.188 秒 |
| 报告输入 / 输出 / 总 token | 23,858 / 17,335 / 41,193 |
| 已核验金额 | 未知 |

关键 H3-R5 返回已购 `met`、未配置 `unmet`、验收状态未登记 `unknown`，待办要求先确认验收是否完成并补充依据，正文没有断言验收未执行或未通过。冲突题也正确描述德国独占与新加坡复制的相反方向，仅在吞吐事项应用优先级。完整[原始运行](../../evaluation/presales_quality_holdout_v3.v8-gateway.json)、[分类/引用评分](../../evaluation/presales_quality_holdout_v3.v8-gateway.score.json)、[明确映射](../../evaluation/presales_quality_v8.prerequisite-review.json)、[逐项评分](../../evaluation/presales_quality_v8.prerequisite-score.json)和[正文审阅](../../evaluation/presales_quality_v8.review.json)分开保留。

这是已知短资料的一次回归和助手非盲审，不是独立领域审查、客户验收、真实检索或稳定性证明。最慢题接近 120 秒截止时间；H3-R1/R5 报告输出 token 分别为 4335/4183，超过请求的 4000，说明该参数不能当作该路由的收费硬上限。六次后已停止，不追加 H4/H1/H2、不覆盖 v7 失败、不部署。后续仍需未知/代表资料验收、领域审核、当前 4C4G 业务容量、告警与恢复验证。
