---
source_type: public_reference_inspired_synthetic
synthetic: true
requires_human_review: true
does_not_close_m5_m7: true
---

# Public-reference-inspired RAG quality suite provenance

## Artifact identity

- Dataset: `evaluation/rag_quality_public_reference_v1.json`
- Corpus: `evaluation/corpus/public_reference_inspired_v1/`
- Fictional organization: Northstar Ledger

All corpus documents, values, roles, scenarios, anchors, questions, and labels in this suite were
written for this repository. Northstar Ledger is not a real organization. The suite contains no
customer data, personal data, real incident data, credentials, endpoints, legal conclusions, or
production measurements.

## Public reference register

| Public reference | Scenario-level inspiration | Content boundary |
| --- | --- | --- |
| [NIST Cybersecurity Framework 2.0](https://www.nist.gov/cyberframework) | General risk-management context for resilience and security scenarios. | No claim of adoption, conformity, certification, or control coverage. |
| [NIST SP 800-61 Rev. 3](https://csrc.nist.gov/pubs/sp/800/61/r3/final) | High-level incident-handling lifecycle concepts. | No guidance, requirements, values, or sentences were copied. |
| [Microsoft Azure: How to read a service-level agreement](https://docs.azure.cn/en-us/reliability/concept-service-level-agreements) | The test distinction between an internal reliability objective and a supplier SLA. | All names, values, evidence fields, terms, and wording are invented; they are not Azure terms. |
| [UConn Incident Response Plan](https://security.uconn.edu/incident-response-plan) | The need to distinguish an observed event from a declared incident. | No contacts, role names, regulated-data details, organization terms, or wording were used. |
| [OWASP LLM01:2025 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) | Indirect-injection scenarios involving imported, encoded, or multilingual content. | Conceptual attribution only. No OWASP text was copied. |

These references informed only the kinds of distinctions worth testing. They are not evaluation
inputs, endorsed controls, proof of compliance, or evidence that the fictional policies implement
the referenced guidance.

## Licensing and copying boundary

An authoritative OWASP licensing page was not confirmed during this research pass: one candidate
returned HTTP 404, and a later search attempt failed at the research provider. Consequently, this
suite uses no OWASP wording. Any future proposal to import text from a public source must verify the
applicable license independently before changing the corpus.

The corpus deliberately uses original fictional wording and invented values. This provenance file
provides attribution for conceptual inspiration; it is not a legal opinion about any source.

## Evaluation and review boundary

This suite can statically exercise the repository loader, stable-anchor references, closed labels,
evidence-only refusal cases, and indirect-injection safety cases. Deterministic matching does not
establish that the labels are semantically complete or that a model response is correct beyond the
listed variants.

Independent human content and semantic review is required before any real-provider trial. A future
gate-closing evaluation would additionally require approved de-identified representative enterprise
material, independently reviewed golden labels, stable provider route and revision evidence,
provider cost evidence, repeatable complete clean runs, and explicit gate review.

Passing this synthetic suite does not demonstrate representative enterprise quality, production
capacity or availability, privacy or compliance, provider stability or cost, or closure of the M5
or M7 external gates.
