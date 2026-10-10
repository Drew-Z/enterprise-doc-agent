from pathlib import Path

import pytest
import yaml
from tests.deployment.test_configure_staging_manifest import (
    _render_browser_manifest,
    _write_template,
)


@pytest.mark.parametrize("primary,fallback", [("true", "false"), ("false", "true"), ("", "")])
def test_question_modes_render_independently_and_update_config_digest(tmp_path, primary, fallback):
    original = _render_browser_manifest(tmp_path)
    documents = _render_browser_manifest(
        tmp_path,
        presales_primary_question_assessment=primary,
        presales_fallback_question_assessment=fallback,
        fallback_model_base_url="https://fallback.invalid/v1",
        fallback_model_name="grok-4.7",
    )
    data = next(d for d in documents if d["kind"] == "ConfigMap")["data"]
    assert data.get("PRESALES__PRIMARY_QUESTION_ASSESSMENT") == (primary or None)
    assert data.get("PRESALES__FALLBACK_QUESTION_ASSESSMENT") == (fallback or None)
    assert data["MODEL__MODEL_NAME"] == "staging-model"
    assert data["MODEL__FALLBACK_MODEL_NAME"] == "grok-4.7"
    before = next(d for d in original if d["kind"] == "Deployment")["spec"]["template"]["metadata"]
    after = next(d for d in documents if d["kind"] == "Deployment")["spec"]["template"]["metadata"]
    assert before["annotations"] != after["annotations"]


def test_defaults_remove_stale_question_modes(tmp_path):
    source = tmp_path / "browser-template.yaml"
    _write_template(source)
    documents = list(yaml.safe_load_all(source.read_text(encoding="utf-8")))
    config = next(d for d in documents if d["kind"] == "ConfigMap")
    config["data"].update(
        {
            "PRESALES__PRIMARY_QUESTION_ASSESSMENT": "true",
            "PRESALES__FALLBACK_QUESTION_ASSESSMENT": "true",
        }
    )
    source.write_text(yaml.safe_dump_all(documents), encoding="utf-8")
    rendered = _render_browser_manifest(tmp_path)
    data = next(d for d in rendered if d["kind"] == "ConfigMap")["data"]
    assert not any(key.endswith("QUESTION_ASSESSMENT") for key in data)


@pytest.mark.parametrize(
    "options",
    [
        {"presales_primary_question_assessment": "yes"},
        {"presales_fallback_question_assessment": "1"},
        {"presales_fallback_question_assessment": "true"},
    ],
)
def test_invalid_question_modes_fail_before_writing(tmp_path, options):
    with pytest.raises(ValueError):
        _render_browser_manifest(tmp_path, **options)
    assert not (tmp_path / "browser-staging.yaml").exists()


def test_workflow_passes_both_question_modes():
    path = Path(__file__).parents[2] / ".github/workflows/deploy-staging.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    step = next(
        s
        for job in workflow["jobs"].values()
        for s in job.get("steps", [])
        if "scripts/configure_staging_manifest.py" in s.get("run", "")
    )
    for route in ("PRIMARY", "FALLBACK"):
        name = f"PRESALES_{route}_QUESTION_ASSESSMENT"
        assert step["env"][name] == "${{ vars.STAGING_" + name + " }}"
        assert "--presales-" + route.lower() + '-question-assessment "$' + name + '"' in step["run"]
