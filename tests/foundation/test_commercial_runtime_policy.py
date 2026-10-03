import asyncio

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings, AuthSettings
from enterprise_doc_core.config import AppEnvironment
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_worker import presales
from enterprise_doc_worker.config import WorkerSettings


@pytest.fixture(autouse=True)
def synthetic_runtime_configuration(monkeypatch):
    for key, value in {
        "DATABASE__URL": "postgresql+psycopg://test:test@127.0.0.1:1/test",
        "OBJECT_STORE__ACCESS_KEY": "test-access",
        "OBJECT_STORE__SECRET_KEY": "test-secret",
        "MODEL__PROVIDER": "openai_compatible",
        "MODEL__BASE_URL": "https://model.example.test/v1",
        "MODEL__API_KEY": "test-key",
        "MODEL__MODEL_NAME": "test-model",
        "EMBEDDING__PROVIDER": "openai_compatible",
        "EMBEDDING__BASE_URL": "https://model.example.test/v1",
        "EMBEDDING__API_KEY": "test-key",
        "EMBEDDING__MODEL_NAME": "test-embedding",
        "MCP__SIGNING_SECRET": "test-signing-only-" * 4,
    }.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("environment", list(AppEnvironment))
async def test_api_composes_entitlement_policy_from_its_real_environment(environment) -> None:
    app = create_app(
        settings=ApiSettings(
            _env_file=None,
            app_env=environment,
            auth=AuthSettings(signing_key=SecretStr("test-policy-only-" * 4)),
        ),
        checkers=[],
    )
    async with app.router.lifespan_context(app):
        assert app.state.usage_service.require_active_entitlement == (
            environment in {AppEnvironment.STAGING, AppEnvironment.PRODUCTION}
        )


@pytest.mark.parametrize("environment", list(AppEnvironment))
async def test_worker_composes_the_same_entitlement_policy(environment, monkeypatch) -> None:
    observed = []

    def capture_generation(generation, gateways):
        observed.append(generation.usage_service.require_active_entitlement)

    monkeypatch.setattr(presales, "BackgroundGeneration", capture_generation)
    shutdown = asyncio.Event()
    shutdown.set()
    await presales.run_presales(
        WorkerSettings(_env_file=None, app_env=environment),
        async_sessionmaker(),
        shutdown,
        MetricsRuntime.create(),
    )
    assert observed == [environment in {AppEnvironment.STAGING, AppEnvironment.PRODUCTION}]
