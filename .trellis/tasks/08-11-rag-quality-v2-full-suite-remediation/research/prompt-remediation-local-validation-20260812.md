# Prompt Remediation Local Validation 2026-08-12

## Scope

The diagnostic report selected only a prompt-first remediation. Dataset v2, retrieval settings,
stable-anchor mapping, citation authorization, graph behavior, and tool schemas were unchanged.

Prompt behavior `m4.v3` adds four requirements:

- state the requested facts completely and explicitly;
- use the minimum sufficient citation set while retaining multiple citations for distinct facts;
- copy each `chunk_id` and `document_version_id` from the same supplied evidence item;
- copy each excerpt as a contiguous verbatim span from that evidence text.

The gateway applies the additions only to persisted prompt version `m4.v3`. A compatibility test
proves `m4.v2` retains the previous contract. `AgentSettings` now defaults to graph `m4.v2`,
prompt `m4.v3`, and tool schema `m4.v2`.

## Red Evidence

Before implementation, the two focused public tests failed because the generated system prompt
did not contain the new requirements and `AgentSettings().prompt_version` was still `m4.v2`.

## Green Evidence

- Focused public prompt/version tests: `3 passed`.
- Complete model gateway, Agent service/graph, and Worker execution tests: `55 passed`.
- Ruff format check: passed.
- Ruff check: passed.
- Mypy on the changed source modules: passed.
- Non-integration suite: `821 passed, 107 deselected`.
- Agent run and graph Worker database integration tests: `13 passed`.

No raw model answer, citation excerpt, runtime identifier, token, or provider payload was retained
in this validation record.
