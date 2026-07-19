from __future__ import annotations

from uuid import uuid4

import pytest

from enterprise_doc_core.agents import (
    AgentArtifactService,
    AgentArtifactStoreUnavailable,
)
from enterprise_doc_core.telemetry import MetricsRuntime


async def test_agent_artifact_service_records_success_and_store_unavailable() -> None:
    metrics = MetricsRuntime.create()
    service = AgentArtifactService(
        session_factory=None,  # type: ignore[arg-type]
        artifact_store=object(),  # type: ignore[arg-type]
        metrics=metrics,
    )

    async def list_success(**_: object) -> tuple[()]:
        return ()

    service._list_for_run = list_success  # type: ignore[method-assign]
    assert (
        await service.list_for_run(
            tenant_id=uuid4(),
            actor_id=uuid4(),
            run_id=uuid4(),
        )
        == ()
    )

    async def download_unavailable(**_: object) -> object:
        raise AgentArtifactStoreUnavailable()

    service._get_download = download_unavailable  # type: ignore[method-assign]
    with pytest.raises(AgentArtifactStoreUnavailable):
        await service.get_download(
            tenant_id=uuid4(),
            actor_id=uuid4(),
            artifact_id=uuid4(),
        )

    rendered = metrics.render().decode("utf-8")
    assert 'boundary="artifact",operation="list",result="success"' in rendered
    assert 'boundary="artifact",operation="download",result="retryable_error"' in rendered
