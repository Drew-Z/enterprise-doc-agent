# 本地提交计划

状态：范围已准备，尚未暂存或执行；本计划仅涉及本地提交。

`feat: deploy private Cloudflare mailbox HTTPS foundation`

一个提交，包含下面 **101 个新增文件**：部署/恢复工具、凭据脚本、公网验收与边界测试、专用规范及本阶段交付证据。当前 HEAD 为 `eca462385da540fef163e80d90e82e55383f05d4`，暂存区为空。

## 验证与恢复

- 2026-09-18 重跑部署边界测试：21 passed；本次 HTTPS 页面 200、未认证邮箱 API 401，线上唯一 100% 版本与原验收一致。
- 原 2026-09-16 全量后端 1356 passed / 312 deselected，公网 21 项通过。此处沿用并核对其证据，不声称本次重跑全量或浏览器。
- 18 份质量日志和 26 份来源记录哈希全部一致；旧提交的 124 个包文件逐字节未变。
- [续接复核](resumption-2026-09-18.json) 记录当前范围、线上只读检查、源码哈希、恢复组验证及临时清理。
- [改动清单](changed-files.json) 为下列文件记录 SHA-256/字节数，自身不递归记录哈希；在批准后暂存前重新比对。
- 原恢复组 `D:\Agent\codex\backups\tasks\20260916T054248.861Z-saas-cloudflare-mail-rollout` 保留，4 份恢复实体重新核验通过。

## 精确新增文件范围

- `.trellis/spec/infrastructure/backend/cloudflare-mail-rollout.md`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/README.md`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/active-version-inspection.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/browser-external-probe.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/changed-files.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/check.jsonl`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/cleanup-plan.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/cleanup-report.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/cloudflare-final-verification.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/cloudflare-preflight.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/commit-plan.md`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/credential-acl-final.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/credential-acl.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/deployment-plan.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/deployment-resume-01.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/deployment-resume-02.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/deployment-resume-03.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/deployment-state.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/design.md`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/dns-https-01.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/public-01/mailbox-credential-gate.png`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/public-01/private-owner-empty-inbox.png`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/public-01/private-site-gate.png`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/public-01/report.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/public-02/mailbox-credential-gate.png`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/public-02/private-owner-empty-inbox.png`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/public-02/private-site-gate.png`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/public-02/report.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/application-mypy.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/backend-tests.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/deployment-tests.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/final-application-mypy.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/final-backend-tests.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/final-mail-mypy.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/final-ruff-check.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/final-ruff-format.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/logs-disable-green.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/logs-disable-red.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/mail-mypy.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/publication-boundaries.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/publication-green.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/publication-red.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/resumption-2026-09-18-deployment-tests.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/ruff-check.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/ruff-format.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/version-readback-green.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/quality/version-readback-red.txt`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/d1-create.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/d1-limits.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/d1-location.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/d1-query.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/email-api.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/email-dns-create.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/email-dns-get.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/email-subdomains.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/static-assets.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/web-analytics-api.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/web-analytics-rule-create.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/web-analytics-rule-list.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/web-analytics-rules.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/web-analytics-site-list.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/web-analytics-site-update.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/web-analytics-start.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/worker-deployments-get.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/worker-deployments-list.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/worker-domain-update.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/worker-logs.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/worker-secret.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/worker-settings-edit.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/worker-settings.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/worker-subdomain.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/worker-upload.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/evidence/sources/worker-version-get.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/implement.jsonl`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/implement.md`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/next-steps.md`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/prd.md`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/prepublication-inspection.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/public-asset-inspection.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/publication-01.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/publication-preflight.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/quality-evidence-index.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/recovery-reference.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/research.md`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/resume-inspection.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/resumption-2026-09-18.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/script-settings-inspection.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/source-index.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/task.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/validation.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/web-analytics-disable-01.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/web-analytics-exclusion-01.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/web-analytics-exclusion-plan.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/web-analytics-inventory.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/web-analytics-rule-diagnostic.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/workspace-baseline.json`
- `.trellis/tasks/09-16-saas-cloudflare-mail-rollout/workspace-preservation.json`
- `infra/cloudflare_mail/deploy.py`
- `infra/cloudflare_mail/new-credentials.ps1`
- `tests/cloudflare_mail/public-site.mjs`
- `tests/cloudflare_mail/test_mail_deploy.py`

## 不纳入本提交的既有工作

本阶段开始前已有 **1128 条脏路径**，完整路径、状态及原始哈希见 [workspace-baseline.json](workspace-baseline.json) 的 `paths`；这些路径全部不在上述提交范围。

其中 1125 条仍与原始基线一致；以下 3 份文件包含此前 WIP 及本阶段有意增量，保留在工作树，本次不暂存整文件：

- `.trellis/spec/foundation-tests/backend/index.md`
- `.trellis/spec/infrastructure/backend/index.md`
- `.trellis/tasks/09-11-saas-first-use/task.json`

本轮不重置、清理或自动提交这些历史内容。后续如需提交其增量，必须另列精确范围。

## 执行条件与下一步

项目 `.trellis/workflow.md` Phase 3.4 第 5 步要求：**“Present the plan once, ask for one-shot confirmation”**。用户确认本计划后，先核对 HEAD、空暂存区、全部新增文件哈希和排除范围，再按逐文件白名单暂存并创建上述单个提交。不得使用 `git add .`、amend 或 push。

收信 Routing 本次实时回读仍为 `disabled/unconfigured`。收信子域控制台开通按 [next-steps.md](next-steps.md) 执行；此提交不等同于真实邮件收发、长期 Keycloak 或完整 SaaS 旅程已验收。
