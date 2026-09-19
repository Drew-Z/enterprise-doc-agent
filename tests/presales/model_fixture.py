"""Controlled responses derived from real retrieved upload evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from uuid import uuid4

import httpx

from enterprise_doc_core.presales.schemas import GenerationInput, ModelDraft
from tests.presales.ingestion_fixtures import UploadFixture


class UploadedEvidenceModel:
    def __init__(
        self,
        fixtures: Mapping[str, UploadFixture],
        *,
        model_name: str = "controlled-ingestion-fixture",
    ) -> None:
        self.fixtures = fixtures
        self.model_name = model_name
        self.calls: list[dict[str, object]] = []

    async def respond(self, request: httpx.Request) -> httpx.Response:
        envelope = json.loads(request.content)
        payload = GenerationInput.model_validate_json(envelope["messages"][1]["content"])
        fixture = next(
            (
                item
                for key, item in self.fixtures.items()
                if key in {"txt", "pdf", "docx"}
                and item.excerpt.split()[0].lower() in payload.requirement.text.lower()
            ),
            None,
        )
        if fixture is None:
            raise RuntimeError("unknown synthetic acceptance requirement")
        evidence = next(
            (item for item in payload.evidence if fixture.excerpt in item["text"]), None
        )
        if evidence is None:
            raise RuntimeError("actual retrieval did not return the required uploaded evidence")
        citation = {
            "chunkId": evidence["chunkId"],
            "documentVersionId": evidence["documentVersionId"],
            "excerpt": fixture.excerpt,
        }
        self.calls.append({"requirementKey": payload.requirement.key, "citation": citation})
        draft = ModelDraft.model_validate(
            {
                "status": "supported",
                "answer": "受控验收输出: " + fixture.excerpt,
                "conditions": [],
                "missingInformation": [],
                "citations": [citation],
            }
        )
        return httpx.Response(
            200,
            json={
                "id": "controlled-ingestion-" + uuid4().hex,
                "model": self.model_name,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": draft.model_dump_json(by_alias=True),
                        },
                    }
                ],
            },
        )
