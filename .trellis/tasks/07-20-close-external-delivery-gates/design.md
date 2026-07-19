# Design: Close External Delivery Gates

## Boundaries

The repository remains the source of truth for build, release, staging and
evidence contracts. Cloud resources, GitHub settings, registry state and
operator credentials remain external inputs. Local tests must validate shape and
ordering without pretending to validate those external systems.

## Supply Chain

Pull-request builds keep a local image path for fast vulnerability feedback.
Tagged releases use one push build and expose its digest. All release scans,
SBOM generation, signing and verification target `image@sha256:digest` from that
push. Verification artifacts are uploaded with `always()` while the job still
fails when a required check fails.

## Web and Staging

The Web Dockerfile accepts `VITE_OBJECT_STORE_ORIGINS` alongside the API base
URL. The staging overlay owns environment-specific public host and object-store
configuration, ingress/TLS wiring, and a named image pull secret contract; no
secret data is committed. The deploy workflow continues to apply prerequisites,
migration, then workloads and captures redacted manifests and rollout evidence.

## Evidence

Recovery and capacity manifests identify environment, cluster, commit, image
digests, operator, time bounds, command artifacts and hashes. A report can only
be `passed` when required measurements and smoke checks exist. Missing external
credentials or managed infrastructure yields `blocked_external` with a reason.

## Rollout Safety

No automatic production action is added. Staging deployment and rollback remain
manual environment workflows. Database restore remains isolated and explicitly
confirmed; rollback records database migration compatibility as a separate
concern.
