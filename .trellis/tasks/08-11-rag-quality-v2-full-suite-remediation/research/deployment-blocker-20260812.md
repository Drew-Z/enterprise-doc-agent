# Diagnostic Deployment Evidence - 2026-08-12

## Completed Evidence

- Diagnostic implementation commit: `a7a5af5` (`feat(rag): add secret-safe quality diagnostics`).
- Self-hosted fallback quality run: `31522014693`, commit `fffa65e`, passed in `43m11s`.
- The passed self-hosted run included pre-provisioned toolchain validation, `uv sync --frozen`, Ruff,
  mypy, the non-integration test suite, pnpm install, lint, typecheck, test, and build.
- The immutable diagnostic release tag `v0.1.26` points to `fffa65e`.
- Local evaluator validation for the seven selected case IDs passed with evaluator `m5.rag-quality.v3`.
  It is validation-only evidence and does not claim staging execution.

## External Blocker

Container Supply Chain run `31526289196` was attempted twice for `v0.1.26` (run attempts 1 and 2).
All four image jobs failed before starting any step (`steps=[]`, no runner assigned). The GitHub
check annotation states: `The job was not started because recent account payments have failed or your
spending limit needs to be increased.`

Therefore no signed immutable image digests exist for this diagnostic version, and `deploy-staging.yml`
was not dispatched. This is an account-level GitHub Actions billing blocker, not an application or
container build failure. The failed runs remain immutable evidence and must not be relabeled as a
quality result.

## Next Gate

After GitHub Actions billing/spending is restored, rerun the existing `v0.1.26` Container Supply Chain
workflow. Do not create a replacement tag unless the source commit changes. Only after all four signed
digests and the release manifest are available may the staging deployment and seven-case real diagnosis
proceed.

## Subsequent Diagnostic-Only Alternative

Later on 2026-08-12, the same `v0.1.26` source was built locally with the reviewed Dockerfiles,
transferred as SHA-256-recorded archives, imported into the staging node's containerd namespace,
and exposed through exact local digest references. Deploy Staging run `31530215417`, attempt 3,
then passed migration, workload rollout, embedding validation, in-cluster readiness, and the
authenticated smoke. The seven-case diagnostic report was produced from that runtime.

This alternative does not close the blocker above. The images were not published to GHCR and do
not have the required Cosign signature, SBOM attestation, or provenance attestation. Detailed
image, deployment, report, and limitation evidence is recorded in
`research/diagnostic-staging-outcome-20260812.md`.
