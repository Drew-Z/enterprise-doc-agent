# Analysis 候选提交计划（未执行）

基线：`codex/presales-prerequisite-assessment` @ `31bc30fb7d65fc763fcb45fe5bd1b1c5ff73d062`。当前计划精确纳入 169 个路径，分为 5 个有序提交；其余 3016 条脏状态全部排除。

## 拟提交顺序

1. `feat(analysis): implement bounded multi-document review workflow`（72 个路径）
   - `apps/api/src/enterprise_doc_api/analysis/__init__.py`
   - `apps/api/src/enterprise_doc_api/analysis/router.py`
   - `apps/api/src/enterprise_doc_api/analysis/runs_router.py`
   - `apps/api/src/enterprise_doc_api/app.py`
   - `apps/api/src/enterprise_doc_api/tenant_usage/router.py`
   - `apps/worker/src/enterprise_doc_worker/__main__.py`
   - `apps/worker/src/enterprise_doc_worker/analysis_handler.py`
   - `apps/worker/src/enterprise_doc_worker/consumer_main.py`
   - `apps/worker/src/enterprise_doc_worker/handler.py`
   - `apps/worker/tests/test_handler.py`
   - `packages/core/src/enterprise_doc_core/analysis/__init__.py`
   - `packages/core/src/enterprise_doc_core/analysis/dependencies.py`
   - `packages/core/src/enterprise_doc_core/analysis/evidence.py`
   - `packages/core/src/enterprise_doc_core/analysis/execution_models.py`
   - `packages/core/src/enterprise_doc_core/analysis/execution_schemas.py`
   - `packages/core/src/enterprise_doc_core/analysis/execution.py`
   - `packages/core/src/enterprise_doc_core/analysis/export.py`
   - `packages/core/src/enterprise_doc_core/analysis/gateway.py`
   - `packages/core/src/enterprise_doc_core/analysis/models.py`
   - `packages/core/src/enterprise_doc_core/analysis/review_schemas.py`
   - `packages/core/src/enterprise_doc_core/analysis/review.py`
   - `packages/core/src/enterprise_doc_core/analysis/schemas.py`
   - `packages/core/src/enterprise_doc_core/analysis/service.py`
   - `packages/core/src/enterprise_doc_core/analysis/tools.py`
   - `packages/core/src/enterprise_doc_core/billing/administration_contracts.py`
   - `packages/core/src/enterprise_doc_core/billing/administration.py`
   - `packages/core/src/enterprise_doc_core/billing/generation_budget.py`
   - `packages/core/src/enterprise_doc_core/billing/product_contracts.py`
   - `packages/core/src/enterprise_doc_core/billing/product_models.py`
   - `packages/core/src/enterprise_doc_core/billing/product_usage.py`
   - `packages/core/src/enterprise_doc_core/billing/provider_calls.py`
   - `packages/core/src/enterprise_doc_core/billing/provider_models.py`
   - `packages/core/src/enterprise_doc_core/config/settings.py`
   - `packages/core/src/enterprise_doc_core/db/metadata.py`
   - `packages/core/src/enterprise_doc_core/db/migrations/versions/20261011_0038_document_structures.py`
   - `packages/core/src/enterprise_doc_core/db/migrations/versions/20261011_0039_analysis_sources.py`
   - `packages/core/src/enterprise_doc_core/db/migrations/versions/20261012_0040_analysis_execution.py`
   - `packages/core/src/enterprise_doc_core/db/migrations/versions/20261012_0041_analysis_agent.py`
   - `packages/core/src/enterprise_doc_core/db/migrations/versions/20261012_0042_analysis_review_delivery.py`
   - `packages/core/src/enterprise_doc_core/db/migrations/versions/20261012_0043_analysis_dependencies.py`
   - `packages/core/src/enterprise_doc_core/documents/envelope.py`
   - `packages/core/src/enterprise_doc_core/documents/ingestion_service.py`
   - `packages/core/src/enterprise_doc_core/documents/models.py`
   - `packages/core/src/enterprise_doc_core/documents/structure_models.py`
   - `packages/core/src/enterprise_doc_core/documents/structure_process.py`
   - `packages/core/src/enterprise_doc_core/documents/structure_store.py`
   - `packages/core/src/enterprise_doc_core/documents/structure.py`
   - `packages/core/src/enterprise_doc_core/operations/cli.py`
   - `packages/core/src/enterprise_doc_core/presales/route_health.py`
   - `packages/core/src/enterprise_doc_core/uploads/completion_records.py`
   - `packages/core/src/enterprise_doc_core/uploads/policy.py`
   - `packages/core/src/enterprise_doc_core/uploads/session_service.py`
   - `scripts/manage_tenant_entitlements.py`
   - `tests/admission/test_platform_operations_cli.py`
   - `tests/analysis/__init__.py`
   - `tests/analysis/conftest.py`
   - `tests/analysis/test_analysis_api_integration.py`
   - `tests/analysis/test_analysis_execution_integration.py`
   - `tests/analysis/test_analysis_invalidation.py`
   - `tests/analysis/test_analysis_migrations_integration.py`
   - `tests/analysis/test_analysis_protocol.py`
   - `tests/analysis/test_analysis_review_delivery.py`
   - `tests/analysis/test_analysis_tools.py`
   - `tests/analysis/test_document_structure.py`
   - `tests/analysis/test_execution_api_integration.py`
   - `tests/analysis/test_source_schemas.py`
   - `tests/analysis/test_source_workspace_integration.py`
   - `tests/analysis/test_structure_ingestion_integration.py`
   - `tests/analysis/test_structured_upload.py`
   - `tests/analysis/test_upload_to_snapshot_integration.py`
   - `tests/billing/test_entitlement_cli.py`
   - `tests/billing/test_entitlement_configuration_contract.py`
2. `feat(web): add document analysis workspace`（23 个路径）
   - `.trellis/tasks/10-11-document-analysis-workspace/evidence/analysis-a4-playwright-20261013-incremental-r4/cleanup.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/evidence/analysis-a4-playwright-20261013-incremental-r4/results.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/evidence/analysis-a4-playwright-20261013-incremental-r4/workspace-prepare-analyze--4eecd-d-and-use-a-narrow-viewport/desktop.png`
   - `.trellis/tasks/10-11-document-analysis-workspace/evidence/analysis-a4-playwright-20261013-incremental-r4/workspace-prepare-analyze--4eecd-d-and-use-a-narrow-viewport/reviewed-desktop.png`
   - `.trellis/tasks/10-11-document-analysis-workspace/evidence/analysis-a4-playwright-20261013-incremental-r4/workspace-prepare-analyze--4eecd-d-and-use-a-narrow-viewport/reviewed-mobile.png`
   - `apps/web/analysis-e2e/teardown.ts`
   - `apps/web/analysis-e2e/workspace.spec.ts`
   - `apps/web/playwright.analysis.config.ts`
   - `apps/web/src/analysis/analysis.css`
   - `apps/web/src/analysis/AnalysisMatrix.test.tsx`
   - `apps/web/src/analysis/AnalysisMatrix.tsx`
   - `apps/web/src/analysis/AnalysisWorkspace.test.tsx`
   - `apps/web/src/analysis/AnalysisWorkspace.tsx`
   - `apps/web/src/analysis/api.ts`
   - `apps/web/src/analysis/executionApi.ts`
   - `apps/web/src/App.tsx`
   - `apps/web/src/product/routes.ts`
   - `apps/web/src/product/tenantUsageApi.ts`
   - `apps/web/src/product/TenantUsagePage.tsx`
   - `apps/web/src/upload/UploadWorkspace.tsx`
   - `apps/web/tsconfig.node.json`
   - `apps/web/vite.analysis.config.ts`
   - `tests/analysis/browser_server.py`
3. `test(analysis): add frozen evaluation and trial packages`（47 个路径）
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/development-01/asana-pricing.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/development-01/asana-subscriber.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/development-01/basecamp-pricing.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/development-01/basecamp-terms.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/development-01/manifest.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/development-01/reference.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/development-01/requirements.synthetic.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/development-01/trello-pricing.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/development-01/trello-terms.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/holdout-football-01/manifest.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/holdout-football-01/README.md`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/holdout-football-01/reference/reference.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/holdout-football-01/reference/review-notes.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/holdout-football-01/reference/review-notes.md`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/holdout-football-01/upload/adidas-orlando-product.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/holdout-football-01/upload/adidas-returns.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/holdout-football-01/upload/nike-returns.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/holdout-football-01/upload/nike-usmnt-product.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/holdout-football-01/upload/puma-mancity-product.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/holdout-football-01/upload/puma-returns.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/holdout-football-01/upload/requirements.synthetic.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/validation-01/manifest.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/validation-01/README.md`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/validation-01/reference/reference.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/validation-01/reference/review-notes.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/validation-01/reference/review-notes.md`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/validation-01/upload/airtable-pricing.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/validation-01/upload/airtable-terms.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/validation-01/upload/clickup-pricing.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/validation-01/upload/clickup-terms.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/validation-01/upload/monday-pricing.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/validation-01/upload/monday-terms.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/materials/validation-01/upload/requirements.synthetic.txt`
   - `.trellis/tasks/10-11-document-analysis-workspace/trial-kit/acceptance-record-template.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/trial-kit/data-and-support.md`
   - `.trellis/tasks/10-11-document-analysis-workspace/trial-kit/feedback-form.md`
   - `.trellis/tasks/10-11-document-analysis-workspace/trial-kit/incident-and-supplement.md`
   - `.trellis/tasks/10-11-document-analysis-workspace/trial-kit/operator-guide.md`
   - `.trellis/tasks/10-11-document-analysis-workspace/trial-kit/README.md`
   - `.trellis/tasks/10-11-document-analysis-workspace/trial-kit/reviewer-guide.md`
   - `.trellis/tasks/10-11-document-analysis-workspace/trial-kit/timing-template.csv`
   - `infra/evaluation/analysis-holdout.example.json`
   - `scripts/evaluate_analysis_holdout.py`
   - `scripts/score_analysis_holdout.py`
   - `tests/analysis/test_a5_materials_contract.py`
   - `tests/analysis/test_analysis_holdout_evaluation.py`
   - `tests/analysis/test_public_materials.py`
4. `feat(ops): add analysis holdout and 4C4G runners`（8 个路径）
   - `infra/capacity/analysis-4c4g.example.yaml`
   - `scripts/capacity_host_probe.py`
   - `scripts/collect_business_telemetry.py`
   - `scripts/run_analysis_capacity.py`
   - `scripts/validate_recovery_capacity_evidence.py`
   - `tests/deployment/test_business_telemetry.py`
   - `tests/deployment/test_run_analysis_capacity.py`
   - `tests/deployment/test_validate_recovery_capacity_evidence.py`
5. `docs(analysis): record candidate scope and validation`（19 个路径）
   - `.trellis/spec/backend/document-analysis-workspace.md`
   - `.trellis/spec/backend/entitlements-usage.md`
   - `.trellis/spec/core/backend/index.md`
   - `.trellis/tasks/09-24-commercial-production-readiness/task.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/a1-negative-coverage.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/a3-comparison.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/a5-candidate.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/check.jsonl`
   - `.trellis/tasks/10-11-document-analysis-workspace/commit-plan.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/commit-plan.md`
   - `.trellis/tasks/10-11-document-analysis-workspace/design.md`
   - `.trellis/tasks/10-11-document-analysis-workspace/evidence/analysis-reviewer-preparation-preflight-20261013.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/evidence/analysis-reviewer-preparation-validation-20261013.json`
   - `.trellis/tasks/10-11-document-analysis-workspace/implement.jsonl`
   - `.trellis/tasks/10-11-document-analysis-workspace/implement.md`
   - `.trellis/tasks/10-11-document-analysis-workspace/prd.md`
   - `.trellis/tasks/10-11-document-analysis-workspace/public-materials.md`
   - `.trellis/tasks/10-11-document-analysis-workspace/task.json`
   - `docs/ops/commercial-product-roadmap-20261011.md`

## 局部暂存

- `scripts/validate_recovery_capacity_evidence.py`：只暂存 A5 runner 阶段基线到当前文件的 analysis-4C4G 增量。
- `tests/deployment/test_validate_recovery_capacity_evidence.py`：同样只暂存 A5 增量。
- 重新以 Git blob 核验后，两份 A5 阶段基线分别与 HEAD 完全一致；因此该局部补丁等同于这两个文件的完整工作区 diff，提交后不会残留更早的未暂存改动。

## 明确排除

- 其余 3016 条脏状态不进入任何提交；精确快照：`D:\Agent\codex\backups\tasks\20260924T015840.986Z-commercial-production-readiness\commit-scope\document-analysis-excluded-status-20261013.txt`，SHA-256 `a71976dec59159487e482674dc3b3b3e655f3c84715de3cfd21f7ef60c37578a`。
- Playwright 中间/失败产物共 23 个被排除；仅保留 r4 的 results、cleanup 与三张验收截图。
- 不 amend、不 push、不部署、不运行真实模型。

## 执行门槛

执行前后都用临时索引核对每组 pathspec、局部补丁和排除快照；每个提交后检查真实 index，最后确认未识别改动仍留在工作区。提交后再规划默认关闭的不可变镜像与受控 staging 留出。
