# Tenant Admission

## Scope / Trigger

Core `admission` provisions a new enterprise from a one-time grant. The current
adapter is a local/test operator CLI restricted to a loopback database. There is
no public admission HTTP route, accept CLI, browser login or production platform
RBAC in this slice. An owner role does not grant platform admission authority.

## Signatures

```python
prepare_admission_credential() -> PreparedAdmissionCredential
TenantAdmissionService(session_factory=..., trusted_issuers=frozenset(...))
await service.issue(operator=..., request=..., credential=...) -> AdmissionSnapshot
await service.show(operator=..., grant_id=...) -> AdmissionSnapshot
await service.revoke(operator=..., grant_id=...) -> AdmissionSnapshot
await service.accept(token=SecretStr(...), identity=..., tenant_name=...) -> AdmissionReceipt
```

`VerifiedAdmissionIdentity` is trusted adapter output, including issuer, subject,
email and email_verified. The caller must verify the actual authentication
protocol before constructing it. A client-supplied boolean is not identity proof.
`PlatformAdmissionOperator` similarly represents a platform-authorized caller;
the local command's operator label is audit attribution, not production RBAC.

## Contracts

- Grants contain a SHA-256 digest of a 32-byte random `adm1_` credential, exact
  issuer, normalized email, expiry, positive storage/seat values and a durable
  consumption receipt. Issuance requires expiry after database time and within
  30 days. Expired pending grants are projected as expired without a cleanup job.
- Accept locks the grant, checks database `clock_timestamp()`, serializes this
  service's email/identity operations with hashed transaction advisory keys, and
  checks time again after resolving the account. lock_timeout is five seconds.
- Tenant, initial owner Membership, explicit ExternalIdentityBinding, initial
  entitlement, grant consumption, grant event and tenant audit commit together.
  User is either new or reused through unambiguous active binding evidence.
  There are no network or file operations inside this transaction.
- Existing email alone never authorizes linking. Cross-tenant identity ambiguity,
  multiple lower(email) matches, inactive users, no active source membership or
  binding, and email changes are account conflicts. Existing rows are not revived
  or rewritten. Lookup is capped at 1,000 bindings; excess requires explicit review.
- Governance locks use Tenant UUID order, then User, Membership and Binding, with
  NOWAIT on existing rows. Legacy tenant-scoped binding semantics remain; advisory
  keys do not claim a global identity invariant across unrelated writers.
- Identical identity and normalized original company name replay the same receipt
  even after company rename or original TTL. Replays revalidate the current
  tenant, user, owner membership, binding and initial entitlement. Missing,
  inactive, reassigned or demoted entities fail closed. Historical receipt UUIDs
  deliberately have no business foreign keys, so deletion cannot reopen a grant.
- Initial storage is applied to Tenant.quota_bytes. seat_limit is only an initial
  configuration snapshot; existing member provision does not enforce it. There
  are no payment, commercial usage or periodic entitlement records here.
- Pre-tenant issued/revoked events have grant scope and record operator/reason.
  Accepted events and tenant audit omit email, subject, credential and caller
  metadata. Grant events have no update/delete service path.
- Migration 0023 adds three tables and two non-unique identity lookup indexes.
  It preserves existing email/binding unique semantics and existing tenant data.

The local command defaults issue/revoke to preview. All limits and the target
credential path are explicit; show is read-only. Example preview in PowerShell:

```powershell
$admissionExpiry = (Get-Date).ToUniversalTime().AddHours(24).ToString('o')
$admissionFile = Join-Path $env:TEMP ('tenant-admission-{0}.json' -f [guid]::NewGuid())
& .\.venv\Scripts\python.exe -X utf8 -B scripts/manage_tenant_admission.py issue `
  --operator local-admin --reason 'Local admission verification' `
  --email owner@example.test --issuer https://identity.example.test `
  --expires-at $admissionExpiry --quota-bytes 1073741824 --seat-limit 1 `
  --credential-file $admissionFile
```

`--execute` is required to write. A prepared credential is exclusively created
and fsynced before issuing its digest. POSIX uses mode 0600; Windows creates an
owner-only protected DACL before writing and requires persistent ACL support.
Existing files, ADS/device/UNC paths and missing parents are rejected. The file
remains `prepared`: its existence never proves database issuance. A failed or
unknown commit returns not_confirmed with grantId and retains the file. Query
show before deciding a recovery action; there is no automatic retry or revoke.

## Validation & Error Matrix

| Input / state | Result |
|---|---|
| Tenant owner passed as operator | AdmissionForbidden |
| Invalid request, TTL or untrusted issue issuer | ValidationError / AdmissionInvalid |
| Unknown, malformed, expired or revoked credential; wrong/unverified identity | AdmissionDenied |
| Same consumed credential, changed identity/name or inactive/missing entity | AdmissionDenied |
| Existing account lacks unambiguous binding evidence | AdmissionAccountConflict |
| Row/advisory timeout, NOWAIT collision, serialization/deadlock failure | AdmissionBusy; explicit retry |
| Changed prepared issuance request or revoke after accept | AdmissionConflict |
| File creation/sync failure | file_failed; databaseWriteAttempted=false |
| Issue/revoke commit result unavailable | not_confirmed; retained credential and grant ID |

Database exceptions are translated without exposing SQL/parameters. The CLI
suppresses raw exceptions and unknown argument text; it has no token argument.

## Good / Base / Bad Cases

- Good: a verified, granted identity opens a company, then opens another company
  through its existing explicit binding while reusing the same active User.
- Base: eight identical accept calls create one company and seven replays;
  revoke and accept serialize to one terminal outcome.
- Bad: matching email without binding, ambiguous legacy bindings, automatic
  account restoration, client-built identity claims or treating seat snapshots
  as enforced subscriptions.

## Tests Required

Contracts and secret handling have unit tests. Atomicity, identity ambiguity,
replay, state constraints, real lock queues, expiry after waiting, and migration
roundtrips use PostgreSQL. CLI tests exercise real private files and a real
commit followed by a simulated lost acknowledgement. Legacy identity/ACL tests
use a separate full-schema database connection scope, preserving their services.
See the foundation admission guide for isolation and cleanup details.

## Wrong vs Correct

Wrong: reuse local bootstrap or provision_member to open an enterprise, add a
fake tenant ID to pre-tenant audit, use transaction-start now() after lock waiting,
or chmod a Windows file after writing its secret.

Correct: a single domain transaction, grant-scoped events, post-lock database
wall time, durable receipt validation and private exclusive file creation before
database issuance. File and database writes remain separate failure domains.

## Proven Examples

- `packages/core/src/enterprise_doc_core/admission/service.py`
- `packages/core/src/enterprise_doc_core/admission/accounts.py`
- `packages/core/src/enterprise_doc_core/admission/credential_file.py`
- `scripts/manage_tenant_admission.py`
- `tests/admission/test_admission_concurrency_integration.py`
- `tests/admission/test_admission_cli_integration.py`
