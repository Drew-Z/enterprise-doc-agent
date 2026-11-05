# 开源组件复用：许可证核查与首轮离线验证

日期：2026-10-11。基线 `21d99196541e224b0b3a66719fc8240b4aa7d9cf`。
结论：可以复用开源组件，但应逐层接入。已实际运行 Haystack 的相邻窗口组件，
证明它能找回部分遗漏原文，也发现直接接入会遇到范围隔离、引用映射和预算问题。
没有证据表明安装一个 RAG 框架就能消除“事实被当成义务”的语义错误。

## 1. 选型与具体复用边界

| 组件与核查版本 | 许可、依赖及本轮证据 | 决定 |
| --- | --- | --- |
| Haystack `3.3.0`，`SentenceWindowRetriever` | [固定版本源码](https://raw.githubusercontent.com/deepset-ai/haystack/v3.3.0/haystack/components/retrievers/sentence_window_retriever.py)、[Apache-2.0](https://raw.githubusercontent.com/deepset-ai/haystack/v3.3.0/LICENSE)；实际包要求 Python >=3.10；隔离 Python 3.12.13 安装并执行成功，无权重 | 优先适配这一组件，不替换现有数据库或完整工作流。 |
| Sentence Transformers `5.2.0` | [依赖声明](https://raw.githubusercontent.com/huggingface/sentence-transformers/v5.2.0/pyproject.toml)、[Apache-2.0](https://raw.githubusercontent.com/huggingface/sentence-transformers/v5.2.0/LICENSE)；Python >=3.10，依赖 torch、transformers、scipy、scikit-learn；ONNX 是可选后端，不能据此宣称基础安装没有 torch | 后续用其 CrossEncoder 比较现有 top10 候选，不自建通用重排框架；本轮未安装、未推理。 |
| Ragas `0.4.3` | [依赖声明](https://raw.githubusercontent.com/vibrantlabsai/ragas/v0.4.3/pyproject.toml)、[Apache-2.0](https://raw.githubusercontent.com/vibrantlabsai/ragas/v0.4.3/LICENSE)；Python >=3.9，包含 datasets、LangChain、instructor 等；[Faithfulness](https://raw.githubusercontent.com/vibrantlabsai/ragas/v0.4.3/src/ragas/metrics/_faithfulness.py)和[FactualCorrectness](https://raw.githubusercontent.com/vibrantlabsai/ragas/v0.4.3/src/ragas/metrics/_factual_correctness.py)源码均有模型生成步骤 | 作为独立离线评估工具，先复用指标定义及来源绑定；不能把它加入线上每题隐式调用。本轮未安装、未生成评分。 |

重排权重候选 [BAAI/bge-reranker-v2-m3 的官方模型卡](https://huggingface.co/BAAI/bge-reranker-v2-m3/raw/main/README.md)
标注 multilingual、Apache-2.0。该卡是本次抓取快照，尚未固定模型 revision 或下载权重，
因此没有完成权重完整性、速度、内存或本项目中文效果验证。不能把库许可代替权重许可，
也不能把模型卡中的“lightweight”直接解释为适合当前4核4GB服务器。

这些许可证允许在遵守条件的情况下商业使用和修改、分发；分发时仍需保留适用许可、
版权与 NOTICE，并标注修改。本轮没有复制第三方实现进产品，也未完成全部传递依赖许可审计。
版本用于可复现核查，不声称全部是最新版本。GitHub git transport 连接失败后未克隆仓库；
固定 tag 的源码/许可通过 smart-search 抓取，实际执行的是固定版本 PyPI 包。

## 2. 实际运行了什么

新增 `scripts/evaluate_haystack_context.py`，只读取既有公开 GitLab 回归资料。
使用原来的1200字符片段、1080步长和历史引用位置作为**受控种子**，调用未改写的
Haystack 3.3.0 `SentenceWindowRetriever` 和 `InMemoryDocumentStore`。
这不是重新运行 PostgreSQL/向量检索，也不是现有 v25 选择器与 cross-encoder 的对照。

分别测试左右1段、左右3段窗口；同时记录原始窗口长度，以及每段1200/1800字符前缀截取的后果。
参考锚点只参与结果检查，不参与种子选择或窗口算法。两题都是已知回归，不能算独立验收集。
本轮没有加载第二供应商资料参与调试，保留其后续扩展验证用途。

| 已知题 | 历史2段是否含关键锚点 | 左右1段是否找回 | 左右3段是否找回 | 原始窗口总长度：1段 / 3段 |
| --- | --- | --- | --- | --- |
| 密码最低长度 | 否 | 是 | 是 | 5,640 / 11,040字符 |
| ISMS范围限制 | 否 | 否 | 是 | 6,720 / 15,360字符 |

历史两段实际总长均为2,400字符。表中窗口长度包含不同窗口之间的重复，正是直接发送这些窗口时
的文本开销；没有以去重长度掩盖增加量。锚点出现只证明该段文字到达，不证明完整回答所需事实都齐全。

**预算检查揭示了不能直接采用的做法：**保留每个窗口前1800字符时，某些关键锚点仍在，
但所有测试组合都不能完整保留两份原始命中。密码题左右3段窗口若各取前1200字符，
连最低长度锚点也再次丢失。不能用“锚点找到”掩盖其他证据被截掉。
所以“扩大窗口，再沿用原前缀截断”没有通过接入标准。

## 3. 隔离、引用与资源检查

每题同时放入同一 source_id、不同租户/版本/generation 的合成干扰片段。
默认 source_id 单字段配置会返回这些干扰项；这是故意构造的配置对照，
不代表 Haystack 承诺提供应用授权后仍发生漏洞。

复用它已有的 `source_id_meta_field` 多字段配置，绑定 tenant_id、document_version_id、
generation_id、source_id 后，返回范围正确。适配层另行验证种子、每段原文及合并后的连续区间。
两题共8项边界检查通过：默认配置干扰对照、缺少generation拒绝、异域种子拒绝、
缺失中间片段时拒绝把拼接内容当连续原文。四个窗口结果均通过范围与原文检查。
这些是实际组件和内存存储的检查，不是实际数据库租户权限或撤销竞争的验收。

在本机 Windows/Python3.12 环境，最终执行进程峰值 working set 约72.2MiB；
该值包含导入和内存数据集，未加载 torch 或权重。单次小样本窗口操作约0.1–0.2ms，
不是性能基准、P95或服务器容量证明。隔离环境实际装入44个包（含测量用psutil）。
对项目环境的安装 dry-run 显示需增加16个包，未实际修改项目环境；
这只是依赖解析观察，不是完整锁文件或生产兼容性验证。

运行前关闭 Haystack telemetry/auto tracing，并安装 Python socket 审计拒绝钩子。
组件实验没有生成、embedding或LLM评审调用。联网抓源码、查资料、下载依赖确实使用网络，
不能称整轮工作“零外部调用”。

## 4. 下一步接入范围与通过标准

1. **保留现有检索，增加小型适配层。**只对已授权候选调用上下文组件，查询也必须限定
   tenant/version/generation，撤销检查仍由项目负责。以原始 chunk ID 和偏移返回引用；
   合并窗口不能冒充某一个旧chunk。先保留原命中，再在显式预算内补邻段，不能默默删掉原证据。
   真实PostgreSQL测试须覆盖范围、版本切换、撤销、超预算和引用回溯，再做真实召回验证。
2. **重排独立做对照。**固定前10候选、证据预算与原始查询，仅改变选择方式。
   前10没有关键段时应修召回，而非要求重排凭空恢复。权重revision、许可与资源测量通过后再选模型。
3. **语义评估单独接 Ragas。**输入原始问题、实际送达的上下文、原始回答和独立参考。
   Faithfulness不衡量漏答，需结合参考事实召回及业务断言。评审也会误判，必须保留分歧和人工复核。
   先冻结独立调用预算及库内部重试/并发配置，不假设“一题一个评分”只花一次模型调用。
4. **业务协议由项目约束。**普通事实问答路径不接收业务前提，条件题保留义务主体、触发和未知状态。
   题型判定需要单独评估。检索或评估框架不会自动修正政策事实被写成规则、未知被写成未完成。

这组工作复用检索/评估通用实现，项目继续负责权限、证据预算、引用身份、业务状态和审核交付。
第二供应商 GitHub Enterprise Cloud 的6题/3文档材料继续用于后续提前冻结的扩展验证，
有真实参与者后再测独立操作与审核耗时。当前没有独立用户验收。

## 5. 复现、验证与恢复

在独立Python3.12环境安装 `haystack-ai==3.3.0`、`psutil==7.0.0` 后运行：

```powershell
& $isolatedPython -B -X utf8 D:\workspace4Cursor\enterprise-doc-agent\scripts\evaluate_haystack_context.py `
  --fixture D:\workspace4Cursor\enterprise-doc-agent\tests\presales\question_context_regressions.json `
  --output C:\path\to\new-context-report.json
```

脚本拒绝覆盖已有输出；每次离线报告记录源码/fixture哈希、包版本、资源值和各臂结果。
首次功能执行已通过；随后只缩短超长注释并通过 Ruff check/format 后再次执行，两个结果分别保留，
未修改数据或通过标准。未改产品源码、依赖锁、默认prompt、模型路由、主备策略或线上配置。
相关现有24项context/collector测试另行验证；不把历史全量测试数量写成本轮结果。

恢复组仍为 `D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness`，
阶段 `oss_component_reuse_20261011`，修改前已有文件由基线提交恢复，新文件登记为原先不存在。
`fallback-model-evidence/oss-reuse-20261011-*` 保留抓取原页、实际源码/许可、安装记录、两次离线结果、
验证和收尾清单。隔离依赖目录属于本轮系统临时目录，取证后删除，不污染项目环境。
原96份试用资料、25份v25证据和2,993项无关Git状态由收尾脚本校验。
本轮仅本地提交，不发布、不部署；整体任务和商业验收保持未完成。
