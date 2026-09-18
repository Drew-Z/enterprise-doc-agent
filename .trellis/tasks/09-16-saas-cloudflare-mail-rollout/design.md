# 设计

## 输入与发布

固定包目录为 ../09-16-saas-cloudflare-mail-package/delivery/bundle。部署程序先校验已知 manifest 哈希、完整文件集合、每个 SHA-256 和大小，把经验证的文件读入内存。仅 Worker JS、网页 assets、数据库 schema/初始设置进入 Cloudflare；许可证和 provenance 保留在正式包，不上传为网页。

使用官方 REST API 发布已编译输入，避免再次安装前端依赖。Cloudflare 客户端只访问固定 https://api.cloudflare.com/client/v4，从进程环境读取 Token，禁止重定向、自动重试和输出原始请求/异常。报告只保留步骤名称、HTTP 状态、数字错误码、明确选取的资源 ID 和配置。

流程为：完整只读冲突检查 → 创建新 D1（apac、read replication disabled）→ 空库核验 → 固定 schema → 固定初始设置 → assets manifest/session/upload → 不带密钥上传 Worker → 关闭 workers.dev/preview 并回读 → 独立 secret bindings → 回读设置 → 最后挂网页 custom domain。每个变更前写 started 状态，成功后记录 ID；异常保留需要人工核对的状态，不自动删除或重试。此工具首次创建专用资源，已有日志不自动续写、已有同名资源不接管。

实际首次发布经历三次资产传输失败后完成上传和三组秘密，停在发布前回读。资产阶段的显式恢复只能复用记录中的空 D1，不能重放初始化。Worker 已上传后改走 `--publish-existing-from`，仅接受停在配置验证、未尝试挂域名的失败记录，并要求显式提供已检查的 deployment ID、version ID 和 script etag。它先验证专用资源、空库与固定设置、当前唯一 100% 版本和可用域名；任何漂移停止。不会重传 assets、Worker 或秘密。

运行配置从 `/deployments` 的第一项确定当前版本，再从 `/versions/{id}` 读取 resources；资产要求 `script_runtime.assets.raw_run_worker_first=true` 且 `serve_directly=false`。Logpush/Tail/Observability 使用正式 `/script-settings`。本次 `observability:null` 不单独解释成已关闭；发布前通过该接口 PATCH 明确关闭日志/追踪采集、持久化及导出，随后回读。若成功的关闭请求后服务仍用 null 表示设置，报告保留 null 和请求已确认两个独立事实。每次发布前后再次核对版本身份。

公网验收额外发现 zone Web Analytics 向浏览器注入统计脚本；它不受 Worker 日志设置控制。新增仅 inbox 的排除规则触及当前规则额度（409/code10012），回读确认未修改原规则。确认该专用 zone 的唯一 DNS 为本轮邮箱入口后，先保存集中恢复快照，再关闭该 zone 的 Web Analytics，其他 5 个站点与既有规则保持不变。此处是验收发现后的有意配置修正；没有扩大成账号级禁用或套餐升级。最终以真实浏览器 0 外部请求验证。

## 凭据

新建 D:\Agent\codex\secrets\docagent-private-mail\credentials.json，创建空文件后先配置受保护 DACL，再写入三个独立 48-byte 随机值（base64url）。允许当前用户和 SYSTEM 读取；不得提交 Git。PASSWORDS/ADMIN_PASSWORDS 是包含一个值的 JSON 字符串数组，JWT_SECRET 是字符串。后续测试邮箱 Address JWT 单独存入同目录受保护文件，不放 URL、截图或日志。CF Token 不复制到这些文件。

## 地区与保留

primary_location_hint=apac 只表达创建主库的位置偏好，read_replication.mode=disabled 防止主动开启全球读副本。D1 接口返回信息和 query served_by_region 若提供则单独记录；Worker/Email 处理不能据此推断固定在新加坡。当前仅空库和合成账号，无正文保留承诺。真实身份邮件和客户材料接入前，另行落实处理地区、保留/删除及长期身份托管。

## 接口边界

网页沿用上游站点密码、管理员密码和邮箱 JWT。预计 API：无站点凭据/错误凭据 401，公开建址 403；管理员 POST /admin/new_address 建立 owner01 和 member01；受权 GET /api/mails 返回空列表。实际响应作为验收依据；不通过写 D1 绕过 API 验证。

邮箱域仍规划 mailtest.ciallobill.ccwu.cc、notify.ciallobill.ccwu.cc。本轮只挂网页域，不用弃用参数、不复制根域 MX、不猜优先级。官方控制台添加子域完成后，才建立精确收件人 → 本 Worker 的规则；不启用 catch-all。

## 恢复

集中恢复组在 recovery-reference.json 指向的位置，保存父任务和两个已脏索引的修改前字节；旧包由已核验 Git commit 恢复。新资源 ID 写入 deployment-state.json。如需下线，先取消本轮新增网页域关联/精确路由，再按资源 ID 处理新 Worker/D1；数据库删除将丢失其内容，需列出实际范围并取得具体删除批准。工具不提供自动销毁。工作区历史未提交文件、已有 CF 资源和旧任务交付保持原样。
