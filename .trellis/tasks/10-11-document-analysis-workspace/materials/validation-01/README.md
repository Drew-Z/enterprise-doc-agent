# validation-01 软件采购验证包

这是候选冻结后的公开资料验证包，包含 ClickUp Business、monday.com Pro 和 Airtable Business 的官网定价与条款快照。它未参与产品模型调参，但由开发团队准备，因此不是独立客户 UAT。

只把 `upload/` 中七个文件交给系统：`requirements.synthetic.txt` 归为共同需求，其余每两份文件归到对应对象。按 `manifest.json` 的十个字段建立 30 个单元。`manifest.json`、本说明和 `reference/` 不得上传或进入模型上下文。

页面是抓取时点的公开正文，不是面向买家的正式报价。地区、税率、实施范围、签署期限和 SLA 缺失时必须保留未知。`reference/reference.json` 目前 30 行均未独立标注，质量分数保持 `null`。

`reference/review-notes.json` 和同目录说明是助手整理的 30 行审阅底稿，提供行号、摘录、条件与判错规则。它们只供独立审阅者在候选运行结束后核验，不是金标、不能预填独立审核，也不得进入系统上传或模型上下文。冻结 manifest、原 reference 和 `upload/` 均未因此修改。

来源 URL、抓取 provider、命令、原始证据位置、正文和原始 JSON 的 SHA-256 均在 `manifest.json`。公开可读不等于取得再发布许可，本包仅限本地验证和受邀试用。
