# Kubernetes 1.36 VAP Parameter Cache Race

Captured: 2026-07-22

## Context

The first staging admission design stored approved routes and image identities in a
ConfigMap selected through `ValidatingAdmissionPolicy.spec.paramKind` and
`ValidatingAdmissionPolicyBinding.spec.paramRef`. On the real K3s
`v1.36.2+k3s1` API server, deleting and quickly recreating the parameter object and
policy bundle could make an otherwise valid request fail before CEL match conditions
were evaluated:

```text
failed to configure binding: no params found for policy binding with Deny parameterNotFoundAction
```

The ConfigMap existed in the API, so retrying or changing apply order could hide the
problem. That makes it unsuitable as a reproducible GitOps/bootstrap contract even
though it fails in the safe direction.

## Upstream Confirmation

Kubernetes issue [#133827](https://github.com/kubernetes/kubernetes/issues/133827)
documents the same batch delete/recreate behavior across multiple Kubernetes releases.
The root cause is a parameter informer cache miss after the object has already been
recreated in API storage.

Kubernetes PR [#134423](https://github.com/kubernetes/kubernetes/pull/134423)
adds a direct dynamic-client lookup when the informer returns `NotFound`. GitHub records
the PR as merged on 2026-06-25 at commit
`400e80fa208a705167672ca09ca5793f36dae1ea`, with milestone `v1.37`. The staging
cluster is Kubernetes 1.36, so the repository cannot assume that fix is present.

## Decision

The staging guardrails contain no `paramKind`, `paramRef` or `params` expressions.
Non-secret approvals now live on the administrator-owned staging Namespace and CEL
reads them through the admission request's `namespaceObject`:

- approved public/object-store/model routes;
- TLS Secret name and single-host database egress CIDR;
- ConfigMap and prerequisite SHA-256 fingerprints;
- current and one reviewed rollback image per application service.

The deployer has only `get` on the Namespace and cannot mutate prerequisite objects.
API keys, Secret values, DSNs, tokens and private keys remain outside annotations.

## Real API Verification

`scripts/verify_staging_admission.py` validates the bundle on the real API server with
unique `verify-*` policy and binding names. It temporarily applies the expected
Namespace approvals, asserts RBAC allow/deny results, and performs server-side dry-run
checks for:

- accepted API Deployment, migration Job and readiness Job;
- rejected ConfigMap mutation;
- rejected unapproved Secret reference;
- rejected attacker-controlled immutable image.

Its `finally` block deletes the isolated cluster-scoped policies and restores every
managed Namespace annotation. A post-run audit found no `verify-*` policy/binding,
temporary approval annotation or test ConfigMap.

## Consequences

- Admission behavior no longer depends on parameter informer convergence.
- The administrator must render and approve prerequisites before the scoped workflow.
- The workflow fail-closes on approval drift and never applies prerequisites itself.
- A successful isolated admission verification is implementation evidence, not proof of
  a public staging rollout, authenticated smoke, rollback or production readiness.
