# 现有域名复用依据

2026-09-18 已盘点七个启用域名。avi.dpdns.org 原兜底为另一 Worker；gamelab.eu.cc、koe.cc.cd、mcv.qzz.io、mut.ccwu.cc、playarchive.eu.cc、playlab.eu.cc 原兜底为转发。playarchive.eu.cc 只有 5 条邮件 DNS，无其他主机记录，两个拟用 DocAgent 地址无冲突。截图零计数不证明从未使用；原配置全部保留。最新完整云端基线仅在受保护恢复组中保存，仓库只保留脱敏结果及哈希。

通过 smart-search fetch '<URL>' --format json --output '<任务内文件>' 获取以下官方文档；本任务保留完整输出，不依赖会话缓存：

- source-multipart.json：https://developers.cloudflare.com/workers/configuration/multipart-upload-metadata/ 。keep_assets 可以替代旧 assets completion token。
- source-worker-update.json：https://developers.cloudflare.com/api/resources/workers/subresources/scripts/methods/update/ 。multipart PUT 更新 bindings，keep_bindings 按类型保留，D1 上传字段 database_id，assets config 可显式保留。
- source-routing-create.json：https://developers.cloudflare.com/api/resources/email_routing/subresources/rules/methods/create/ 。literal/to 匹配完整地址，worker action 指定既有 Worker 名称。

正式接口实测：/email/routing 返回 enabled 与 status=ready；/email/routing/dns 返回记录列表。第一次只读快照尝试误用了 object 解码，停止且未保存基线；修正后完整捕获成功。DNS ready 不从记录列表猜测。

固定 worker.js 的 _U=/[^a-z0-9]/g；newAddress 在 admin 路径也先清洗名称。第一次新域创建得到 docagentowner01，故选用 docagent 前缀；D1 id=3 通过原 show_password 接口恢复 JWT，避免重建与删除。原失败报告保留。worker 程序及哈希不变。

执行 helper 为 cloud_operations.py，通过 runpy 从仓库根目录启动，复用 deploy.prepare、Api、verify_worker 等现有实现；不是通用配置更新器，也不重放首次部署。每次 state 文件必须全新，超时或身份漂移后需读记录再选择后续操作。
