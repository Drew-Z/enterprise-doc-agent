# Explicit public business sampling

`BusinessPlan` and `StagingBusinessPlan` keep their loopback-only API contract.
Only an operator-selected `PublicStagingBusinessPlan` accepts an HTTPS API origin,
which must match its separately declared `approved_api_origin`. The execution
envelope must bind that origin to the verified deployment before token issuance.
Normal local CLI entrypoints do not select this plan type.

`BusinessIO` obtains the API base URL through the plan's `api_origin` method.
Before an authenticated request, it verifies the resolved request origin and
rejects userinfo or a different scheme/host/port. API redirects remain disabled;
the separate object client has no application Authorization header. The existing
R2 origin, request count, stage timeout, total deadline and failure accounting
remain in force.

Record the actual producer location as well as the public URL. A request from a
server Pod through the public edge is public-path evidence, not browser latency
from an end user's network. A four-task diagnostic is not two complete capacity
rounds, and moving an existing failure to a public runner does not make it pass.

Tests in `test_public_staging_business_capacity.py` exercise the real HTTP client
with a controlled transport: approved URL selection, old-entrypoint rejection,
unauthenticated object requests, no redirected request, and rejection of absolute
cross-origin/userinfo requests before the transport is called.
