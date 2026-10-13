from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError
from scripts.business_capacity import BusinessPlan


def payload():
    return {
        "schema_version": 1,
        "base_url": "http://127.0.0.1:8000",
        "object_origins": ["https://" + "a" * 32 + ".r2.cloudflarestorage.com"],
        "repetitions": 2,
        "phases": [
            {"name": name, "tasks": 1, "concurrency": 1}
            for name in ("ramp", "steady_state", "burst", "recovery")
        ],
        "cases": [
            {
                "key": "txt",
                "path": "source.txt",
                "sha256": hashlib.sha256(b"Fact.").hexdigest(),
                "size_bytes": 5,
                "media_type": "text/plain",
                "query": "Fact?",
                "excerpt": "Fact.",
            }
        ],
    }


def test_staging_plan_accepts_explicit_r2_but_local_plan_still_rejects_it():
    from scripts.staging_business_capacity import StagingBusinessPlan

    value = payload()
    with pytest.raises(ValidationError):
        BusinessPlan.model_validate(value)
    plan = StagingBusinessPlan.model_validate(value)
    assert plan.object_origin(plan.object_origins[0]) == value["object_origins"][0]


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://example.com",
        "https://user@" + "a" * 32 + ".r2.cloudflarestorage.com",
        "https://" + "a" * 32 + ".r2.cloudflarestorage.com/path",
    ],
)
def test_staging_plan_rejects_unapproved_object_origin_shapes(origin):
    from scripts.staging_business_capacity import StagingBusinessPlan

    value = payload()
    value["object_origins"] = [origin]
    with pytest.raises(ValidationError):
        StagingBusinessPlan.model_validate(value)
