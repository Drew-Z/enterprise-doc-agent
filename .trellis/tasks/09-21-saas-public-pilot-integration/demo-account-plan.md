# 演示企业与账号准备

用户询问是否需要默认演示账号。采用受控演示企业和两个独立身份；先供操作者向客户演示，
真实试用者再通过正式准入开户。此文件是开通准备，尚未在线创建企业、成员或额度。

| 项目 | 准备值 |
| --- | --- |
| 演示企业显示名 | DocAgent 演示空间 |
| 演示管理员邮箱 | docagentowner01@playarchive.eu.cc |
| 演示成员邮箱 | docagentmember01@playarchive.eu.cc |
| 登录 | 当前优先 Cloudflare Access 邮箱验证码；身份声明验收尚未完成 |
| 初始资料 | 现有公开试用包 `trial-kit/02-metabase/sources` 中 4 份 TXT |
| 初始问卷 | 同案例 `questionnaire.md`；按现有工作台逐条录入 |
| 权益 | 正式平台运营命令配置有限周期及试点额度，沿用已批准的模型范围 |

执行顺序：

1. 完成真实身份验证，发布包含首次使用功能的新业务版本。
2. 用正式 `enterprise_doc_core.operations admission issue` 为管理员签发准入，首次登录
   后接受准入创建演示企业；以服务器真实返回的 tenant ID 为准。
3. 通过企业成员邀请接入 member，核验角色和数据访问边界；邮箱存在并不自动成为产品用户。
4. 查询 tenant ID 并配置有限周期权益。权益确认后按现有试点配置启用模型生成。
5. 上传 4 份公开资料，等待 ready；导入/录入问卷，完成生成、引用核验、人工复核与 CSV 导出。
   参考答案不作为输入资料，不把预填答案展示为模型生成结果。

资料来源：`../09-16-saas-public-trial-kit/trial-kit/README.md` 及 `02-metabase` 案例。
正式命令和恢复步骤见 `docs/ops/platform-operations.md` 与 `docs/ops/public-pilot-runbook.md`。
演示身份使用真实邮箱验证和产品会话，不通过数据库插入模拟身份或共享默认管理员密码。
