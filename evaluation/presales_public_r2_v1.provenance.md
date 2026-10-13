# R2 官方公开资料业务验收题组

资料来自 Cloudflare 官方产品文档，获取于 2026-09-28（Asia/Shanghai）。问题为助手
拟定的业务场景，并不代表真实客户当前配置。参考判断在首次推理前冻结，待用户审核；
它不是独立专家 gold，也不等于正式客户验收。

| 来源 | 涵盖的资料 |
|---|---|
| [R2 Consistency model](https://developers.cloudflare.com/r2/reference/consistency/) | 直接读写、删除与缓存例外 |
| [R2 Data location](https://developers.cloudflare.com/r2/reference/data-location/) | 位置提示、司法辖区限制与访问配置 |
| [R2 S3 API compatibility](https://developers.cloudflare.com/r2/api/s3/api/) | GetBucketAcl 未实现 |

输入仅保存逐字、有限摘录，共三份，分别 672、581、188 字符。每份记录来源 URL、
UTC 获取时间、抓取回执 SHA-256、提取正文 SHA-256 和实际输入摘录 SHA-256。
完整抓取回执保存在本任务集中证据目录，没有将整页产品资料复制进仓库。
旧 S3 参考地址返回的 404 页面已排除，保留失败回执。

输入文件：`presales_public_r2_v1.json`。
参考文件：`presales_public_r2_v1.gold.json`，绑定输入原始字节的 SHA-256。
模型执行器只读取输入文件，评分阶段才读取参考。

| 题号 | 预期分类 | 必须核验的含义 |
|---|---|---|
| R2-R1 | supported | 直接 S3 读写的强一致性；不扩张到缓存或业务系统 |
| R2-R2 | contradicted | GetBucketAcl 未实现，不能用“S3 兼容”概括全部接口 |
| R2-R3 | conditional | 缓存清除明确尚未执行，应为 unmet |
| R2-R4 | conditional | 桶 jurisdiction 和 S3 endpoint 配置均无记录，应为 unknown |
| R2-R5 | insufficient_evidence | 资料未保证整机损坏后的五分钟业务 RTO，不能编造承诺 |

官方资料支持产品事实；题目中的删除/缓存等事实仅为显式假设。客户配置未知不能
判断为未完成，技术驻留能力也不等于完整法律合规。所有题目、失败和未执行项进入
分母；不得依据模型回答修改参考后声称首次通过。

本题组先用生成层验证语义，每题最多一次、共五次、单路、120 秒、无自动重试或切换。
它不证明文档上传、检索、浏览器体验、生产容量或客户领域的整体质量。
未经核实的供应商费用保持未知；`max_tokens` 不构成金额保证。
