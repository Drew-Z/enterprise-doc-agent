from importlib import import_module

import httpx
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData

from enterprise_doc_core.analysis.schemas import AnalysisSpec, ComparisonObject, default_fields
from enterprise_doc_core.analysis.service import AnalysisWorkspaceService
from enterprise_doc_core.db.metadata import metadata
from tests.analysis.test_analysis_execution_integration import response, setup
from tests.analysis.test_source_workspace_integration import seed_source

pytestmark = pytest.mark.integration
NAMES = {
    "analysis_projects",
    "analysis_source_snapshots",
    "analysis_snapshot_sources",
    "document_structures",
    "document_ingestion_generations",
    "analysis_runs",
    "analysis_units",
    "analysis_actions",
    "analysis_cell_revisions",
    "analysis_events",
    "analysis_input_requests",
    "analysis_tool_executions",
    "analysis_reviews",
    "analysis_artifacts",
    "analysis_approvals",
    "analysis_dependencies",
    "provider_dispatches",
    "product_quotas",
    "product_usage_reservations",
}


async def test_additive_migrations_match_models_and_refuse_history_loss(browser_db) -> None:
    structure = import_module(
        "enterprise_doc_core.db.migrations.versions.20261011_0038_document_structures"
    )
    analysis = import_module(
        "enterprise_doc_core.db.migrations.versions.20261011_0039_analysis_sources"
    )
    execution = import_module(
        "enterprise_doc_core.db.migrations.versions.20261012_0040_analysis_execution"
    )
    agent = import_module("enterprise_doc_core.db.migrations.versions.20261012_0041_analysis_agent")
    review = import_module(
        "enterprise_doc_core.db.migrations.versions.20261012_0042_analysis_review_delivery"
    )
    dependencies = import_module(
        "enterprise_doc_core.db.migrations.versions.20261012_0043_analysis_dependencies"
    )

    def migrate(connection, module, direction):
        with Operations.context(
            MigrationContext.configure(connection, opts={"target_metadata": metadata})
        ):
            getattr(module, direction)()

    # The fixture owns an isolated schema. Exercise both empty rollbacks then the
    # actual 0037 -> 0038 -> 0039 upgrades; never target the public schema.
    async with browser_db.engine.begin() as connection:
        await connection.run_sync(migrate, dependencies, "downgrade")
        await connection.run_sync(migrate, review, "downgrade")
        await connection.run_sync(migrate, agent, "downgrade")
        await connection.run_sync(migrate, execution, "downgrade")
        await connection.run_sync(migrate, analysis, "downgrade")
        await connection.run_sync(migrate, structure, "downgrade")
        await connection.run_sync(migrate, structure, "upgrade")
        await connection.run_sync(migrate, analysis, "upgrade")
        await connection.run_sync(migrate, execution, "upgrade")
        await connection.run_sync(migrate, agent, "upgrade")
        await connection.run_sync(migrate, review, "upgrade")
        await connection.run_sync(migrate, dependencies, "upgrade")

        def differences(sync_connection):
            expected = MetaData(naming_convention=metadata.naming_convention)
            for table in metadata.sorted_tables:
                table.to_metadata(expected)
            context = MigrationContext.configure(
                sync_connection,
                opts={
                    "compare_type": True,
                    "include_object": lambda obj, name, kind, reflected, compare_to: (
                        kind != "table" or name in NAMES
                    ),
                },
            )
            return compare_metadata(context, expected)

        assert await connection.run_sync(differences) == []

    tenant, actor, version, _ = await seed_source(browser_db.sessions)
    service = AnalysisWorkspaceService(session_factory=browser_db.sessions)
    project = await service.create_project(
        tenant_id=tenant,
        actor_id=actor,
        title="保留历史",
        spec=AnalysisSpec(
            objects=(ComparisonObject(id="a", name="A", source_version_ids=(version,)),),
            fields=default_fields(),
        ),
    )
    await service.freeze_sources(
        tenant_id=tenant, actor_id=actor, project_id=project.id, expected_revision=1
    )
    for module, reason in (
        (analysis, "analysis_history_present"),
        (structure, "document_structure_history_present"),
    ):
        with pytest.raises(RuntimeError, match=reason):
            async with browser_db.engine.begin() as connection:
                await connection.run_sync(migrate, module, "downgrade")
    assert (
        len(await service.list_snapshots(tenant_id=tenant, actor_id=actor, project_id=project.id))
        == 1
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        execution_service, run_kwargs, _ = await setup(browser_db.sessions, client)
        run = await execution_service.create_run(**run_kwargs)
        with pytest.raises(RuntimeError, match="analysis_dependency_history_present"):
            async with browser_db.engine.begin() as connection:
                await connection.run_sync(migrate, dependencies, "downgrade")
        matrix = await execution_service.matrix(
            tenant_id=run_kwargs["tenant_id"],
            actor_id=run_kwargs["actor_id"],
            run_id=run.id,
        )
        assert matrix.run.id == run.id and matrix.run.total_units == 1
