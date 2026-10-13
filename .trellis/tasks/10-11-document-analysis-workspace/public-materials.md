# 公开资料开发、验证与留出边界

2026-10-12，开发包 `materials/development-01/`。六份供应商官网提取文本、一份明确合成的采购需求，共三个比较对象、十字段、三十单元。

| 对象 | 方案来源 | 商业条款来源 |
| --- | --- | --- |
| Asana Starter | https://asana.com/pricing | https://asana.com/terms/subscriber-terms |
| Trello Standard | https://trello.com/pricing | https://www.atlassian.com/legal/cloud-terms-of-service |
| Basecamp Studio | https://basecamp.com/pricing | https://basecamp.com/about/policies/terms |

`manifest.json` 保存实际命令 `smart-search fetch URL --format json`、提取工具、抓取时间、URL、原始证据路径与 SHA-256、上传文本 SHA-256 和字节数。正文以抓取结果原样写入 UTF-8 TXT，没有从搜索模型的总结生成报价。原始 JSON 保留在集中任务证据目录。

这些是官网页面的本地提取快照，**不是供应商对该买家的正式报价**。公开可读不代表取得再发布许可，当前只用于本地研发验证，不上传远端发布资料包。日期是本次抓取时间，不表示价格适用于其他日期或地区。HTML 提取后的 TXT 只能验证文本链路，不能证明 PDF、DOCX、XLSX 原始布局解析质量。

操作：上传六份产品/条款 TXT 和 `requirements.synthetic.txt`，共同需求单独分组，另外每两份归到对应对象。对象名使用完整套餐名，十字段维持默认清单。保存后确认来源，再启动取证。`manifest.json`、`reference.json` 和本说明不进入模型输入。

保留的实际缺口：

- Basecamp 页面提取没有套餐金额，不从记忆补价，也不能把缺失读为免费。
- Asana 的 consumer terms 与 subscriber agreement 不混用。已另抓 subscriber agreement；初抓 consumer terms 只作为研究证据保留。
- Asana 的 `$` 符号不自动等于已核实 USD。Trello 文本明确标为 USD。
- 支持时段、席位计费、税费、续约和退款适用条件不能跨套餐拼接。Basecamp 年付退款与一般月付规则应保留各自条件。
- 实施报价、签署订单、采购司法管辖区、税率及准确交付期仍缺失。

参考文件保留完整三十行分母：九行是助手初步标注、二十一行未标注；它不是完整金标，也不产生质量分数。独立复核前不得以这份参考宣称达到 80% 事实正确率。材料已经参与开发分析，只能用于开发或回归。

拆分进度：开发 1 包、验证 1 包、留出 1 包。`validation-01` 使用 ClickUp Business、monday.com Pro 和 Airtable Business 的六份官网快照；`holdout-football-01` 使用 Nike、adidas 和 PUMA 的商品与退货页面，作为足球球衣采购跨领域留出。两包各有三对象、十字段、三十行未标注 reference；reference 与 `upload/` 物理分离，质量分数保持 null。

足球包只扩大试用范围并检查通用字段/证据模型是否过拟合，不把产品主线改成足球专用 Agent。PUMA 搜索摘要曾称 60 天退货，而冻结官网正文写 30 天；只有官网正文可作为候选证据。Nike 退货快照没有政策正文，adidas 退货快照是软 404，均须作为抓取缺口和干扰负例保留。两包公开页面均不是正式报价，且未取得再发布许可，只用于本地评估与受邀试用。

当前模型质量评测未运行；受控 HTTP 的工程测试和浏览器操作不算独立客户试用。试用指南、计时、反馈、错误/补料/退出、数据许可与验收模板已备齐，当前独立参与者为 0。

2026-10-13 已为验证包和足球留出包各新增一份 30 行 `reference/review-notes.json` 助手审阅底稿，逐行记录冻结原文位置、条件、缺口及严重错误规则，并配套独立审阅指南。它们不修改已冻结的 manifest、原 reference 或 `upload/` 哈希，不进入模型输入；助手整理不构成独立标注，`independently_reviewed` 继续为 `false`，`quality_score` 继续为 `null`。

同日留出预检仍为 `blocked`：当前入口缺少 analysis 认证 token，analysis execution 与 structured uploads 未启用，`analysis-primary` 模型路由未配置。预检没有发出网络、模型、embedding 或评分请求。真实运行前还需冻结预算并核对当日余额；不能以本地材料和 runner 验证替代真实模型质量。
