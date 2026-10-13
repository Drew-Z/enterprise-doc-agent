"""Controlled responses derived from real retrieved upload evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from uuid import uuid4

import httpx

from enterprise_doc_core.presales.citation_selection import SelectionDraft, SelectionInput
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
        payload = SelectionInput.model_validate_json(envelope["messages"][1]["content"])
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
        evidence = next((item for item in payload.evidence if fixture.excerpt in item.text), None)
        if evidence is None:
            raise RuntimeError("actual retrieval did not return the required uploaded evidence")
        citation = {
            "citationId": evidence.citation_id,
            "source": evidence.source.model_dump(by_alias=True),
            "excerpt": fixture.excerpt,
        }
        self.calls.append({"requirementKey": payload.requirement.key, "citation": citation})
        draft = SelectionDraft.model_validate(
            {
                "status": "supported",
                "prerequisites": [],
                "answer": "受控验收输出: " + fixture.excerpt,
                "missingInformation": [],
                "citations": [{"citationId": evidence.citation_id}],
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
