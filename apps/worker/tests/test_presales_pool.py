import pytest
from pydantic import ValidationError

from enterprise_doc_worker.config import WorkerServerSettings, WorkerSettings


def test_background_concurrency_has_a_safe_default_and_explicit_environment_override(monkeypatch):
    assert WorkerServerSettings().presales_concurrency == 1
    monkeypatch.setenv("WORKER__PRESALES_CONCURRENCY", "2")
    assert WorkerSettings(_env_file=None).worker.presales_concurrency == 2


@pytest.mark.parametrize("value", [0, 5, 1.5])
def test_background_concurrency_rejects_unbounded_or_fractional_values(value):
    with pytest.raises(ValidationError):
        WorkerServerSettings(presales_concurrency=value)
