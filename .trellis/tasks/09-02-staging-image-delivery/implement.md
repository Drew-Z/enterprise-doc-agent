# Implementation Plan: Bounded 4C4G Worker Delivery

## Behavior Slices

1. [x] Red: extend the deployment contract to require a 1800-second Worker
   progress deadline in the rendered `single-node-4c4g` workload. Green: add
   the narrow strategic-merge patch in the overlay and run the focused test.
2. [x] Red: require the staging workflow to use an 1800-second Worker rollout wait
   both after workload apply and during 4C4G restoration, retaining 600 seconds
   for API, Consumer, and Web. Green: make the workflow and contract test agree.
3. [x] Red: require the 4C4G runbook to expose the reviewed restricted-network
   relay fallback and its safety invariants. Green: add the concise operator
   section and run the documentation contract checks.
4. [x] Full validation: render the 4C4G overlay, run affected deployment tests,
   inspect the workflow statically, and run the appropriate lint/type checks.
5. Controlled release validation: dispatch the immutable `v0.1.33-rc.2`
   staging workflow, retain sanitized evidence, then perform the remaining
   public and UI acceptance gates.

## Files Expected To Change

- `infra/k8s/overlays/single-node-4c4g/resources-patch.yaml`
- `.github/workflows/deploy-staging.yml`
- `tests/deployment/test_m6_contracts.py` or a focused deployment contract
  test, depending on the existing test organization
- `docs/ops/single-node-4c4g-staging-runbook.md`

## Validation Commands

```powershell
uv run pytest tests/deployment/test_m6_contracts.py -q
uv run pytest tests/deployment/test_embedding_rollout_capacity_contract.py -q
kubectl kustomize infra/k8s/overlays/single-node-4c4g | Out-Null
docker run --rm -v "${PWD}:/repo" -w /repo rhysd/actionlint:1.7.7
```

Run any narrower new test before these full affected checks. Do not dispatch
staging until the local checks are green and the user reviews this plan.

## Release Checkpoints

- Inspect live pods, jobs, deployment availability, and public readiness before
  dispatching.
- Record the exact immutable digests and deployment profile without printing
  credentials or smoke tokens.
- Stop after any failed workflow gate. Use the relay path before retrying an
  uncacheable image; do not repeatedly deploy against an unhealthy registry.
- Recheck public health plus all six Chinese and English console routes only
  after authenticated smoke succeeds.
