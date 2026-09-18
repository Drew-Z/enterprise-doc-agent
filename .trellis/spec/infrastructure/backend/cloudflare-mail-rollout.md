# Private Cloudflare mailbox rollout contract

## 1. Scope / Trigger

`infra/cloudflare_mail/deploy.py` provisions the fixed [private mailbox package](./cloudflare-mail.md).
`new-credentials.ps1` owns Windows private-file ACLs. `tests/cloudflare_mail/public-site.mjs`
checks actual HTTPS/API/Chromium behavior without sending or injecting mail.

The rollout targets the selected account, `ciallobill.ccwu.cc`, dedicated Worker/D1
`docagent-private-mail`, and web hostname `inbox.ciallobill.ccwu.cc`. Public mailbox
access does not establish Email Routing/Sending, durable Keycloak hosting, the
complete product HTTP/session journey, customer trials, or fixed processing regions.

## 2. Signatures

```powershell
# Immutable local input check, then read-only conflict preflight.
& .\.venv\Scripts\python.exe -B -m infra.cloudflare_mail.deploy --bundle $bundle
& .\.venv\Scripts\python.exe -B -m infra.cloudflare_mail.deploy --bundle $bundle --preflight

# First provisioning only: refuses existing names and evidence.
& .\.venv\Scripts\python.exe -B -m infra.cloudflare_mail.deploy --bundle $bundle --apply --secrets-file $privateFile --state $newReport

# Explicit assets recovery only before Worker upload, from an inspected failed report.
& .\.venv\Scripts\python.exe -B -m infra.cloudflare_mail.deploy --bundle $bundle --apply --secrets-file $privateFile --state $newReport --resume-assets-from $assetFailure

# Existing Worker publication: no secret file, new database, initialization or upload.
& .\.venv\Scripts\python.exe -B -m infra.cloudflare_mail.deploy --bundle $bundle --publish-existing-from $verificationFailure --expected-version-id $versionId --expected-deployment-id $deploymentId --expected-script-etag $etag --state $newReport

& .\infra\cloudflare_mail\new-credentials.ps1 -CheckOnly -Path $privateFile
& node .\tests\cloudflare_mail\public-site.mjs --repo $repo --credentials $privateFile --mailboxes $mailboxFile --evidence $newEvidence
```

The CLI uses `CLOUDFLARE_API_TOKEN` from the process only. `prepare(bundle)` verifies
the fixed manifest SHA, every file hash/size, complete file set and no links/junctions,
then retains validated bytes. Its alternate manifest anchor is for synthetic unit
fixtures; the CLI provides no alternate trust anchor.

## 3. Contracts

- Use the selected account/zone constants and HTTPS Cloudflare API only. No redirects,
  automatic retries or DELETE operation; response size and time are bounded.
  Multipart uploads use 120 seconds, other requests 40 seconds, `Connection: close`.
- Persist `started` before each mutation, then `succeeded`. State path must be new;
  atomic updates use a transient same-directory file and `os.replace`. An exception
  leaves `failed_needs_inspection`, known resource IDs and safe HTTP/error-code facts.
  Never save response bodies, credentials or raw transport exceptions by default.
- First provisioning checks all list pages and existing D1/Worker/domain/DNS names.
  Create D1 with APAC location hint and disabled read replication, verify it, then
  initialize fixed SQL once. Do not use a pre-existing unrelated database.
- Asset sessions use a separate short-lived JWT and base64 file bodies. Content
  hashes allow a new inspected session to reuse completed assets. No session JWT is
  written to evidence. A transport timeout is not proof that a remote write failed.
- Upload the Worker without secrets, close workers.dev/previews and verify them,
  install three independent secret bindings, explicitly disable logs, verify the
  active version/configuration, then attach the web custom domain.
- A published Worker cannot use asset recovery. `publish_existing` requires a failed
  report that completed Worker/secret uploads and stopped at configuration verification
  before attempting domain attachment. It checks the recorded D1 and empty data,
  fixed settings, inspected deployment/version/etag, and free hostname. It performs
  only a read-only D1 query, logging PATCH, and custom-domain PUT.
- The active deployment is `GET .../deployments` → `result.deployments[0]`. Require
  one version at 100%. `GET .../versions/{id}` supplies `resources.bindings`, script
  handlers/etag, and runtime. Actual assets fields are `raw_run_worker_first=true`
  and `serve_directly=false`; do not look for upload metadata in `/settings`.
- Formal `GET/PATCH .../script-settings` owns Logpush/Tail/Observability. Explicitly
  disable logs/traces, persistence, sampling and exports. Preserve a null readback;
  accept null only after an acknowledged explicit disable in the current operation.
  Reject enabled logging/exports and changed deployments before publication.
- Windows private files have protected DACLs with exactly current-user/SYSTEM
  FullControl. Generate three distinct 48-byte random secrets. Create/protect an
  empty file before writing; never replace existing credentials. Mailbox JWTs use a
  separate protected file, written in place after each successful admin creation.
- Public harness follows no redirects, authenticates via headers/forms, creates or
  reuses only owner01/member01 synthetic mailboxes, and tests empty inboxes/zero send
  balances. No JWT in URLs, screenshots, traces, or browser environment. Chromium
  receives an explicit OS-variable allowlist and blocks/counts external requests.
- Web Analytics is separate from Worker logs. Automatic zone injection may occur
  for browser HTML even when an ordinary HTTP response matches the original bytes.
  A request to `static.cloudflareinsights.com` is a failed zero-external-request gate,
  including when blocked. Do not exempt analytics to turn a failed test green.
- If a targeted analytics rule hits the current quota, do not upgrade a plan or
  change unrelated zones. A dedicated zone may explicitly disable Web Analytics
  after preserving its settings centrally and checking it has no other active host.
  `PUT /rum/site_info/{site_id}` uses `auto_install=true, enabled=false`; verify
  `ruleset.enabled=false`, unchanged other sites/rules, and a new real browser run.
  `lite=true` means EU exclusion, not global disable.
- Email Routing subdomain onboarding follows the supported Dashboard flow. Do not
  use the deprecated `subdomain` DNS query parameter, infer a creation endpoint,
  copy root MX/SPF/DKIM, or guess MX priorities. Keep delivery and Sending separate.
- D1 hint and observed SIN/APAC queries do not promise fixed processing or retention.
  Disabling logging/statistics does not remove mailbox bodies from D1.

## 4. Validation & Error Matrix

| Condition | Result |
| --- | --- |
| Altered manifest/file, extra file or linked input | Reject before a cloud request |
| Name conflict, wrong account or incomplete pagination | Stop before provisioning |
| Weak/reused secrets or existing evidence | Reject before cloud writes |
| Unexpected D1 replication/initial contents | Stop before schema initialization |
| Secret upload fails | No domain publication; preserve safe failure evidence |
| Active version/etag changes or traffic is split | No publication; no resource/secret replay |
| Null observability without explicit disable acknowledgement | Verification fails |
| Logging PATCH times out or readback stays enabled | No domain PUT; inspect uncertain state |
| Direct asset serving or unexpected bindings/handlers | Publication fails |
| Missing/wrong site/admin/mailbox credential | Corresponding API returns 401 |
| Public registration/address creation | 403 |
| Browser attempts any external request or has a page error | Harness fails; retains report and closes browser |
| Cleanup fails | Harness cannot mark passed |

## 5. Good / Base / Bad Cases

- Good: fixed package, dedicated resources, pinned current version, protected secrets,
  explicit log disable, scoped analytics control, genuine HTTPS/browser/D1 evidence.
- Base: preparation only; cloud resources do not exist, or a prior failure requires
  inspection before choosing the corresponding narrow recovery operation.
- Bad: rerunning first deployment after partial success; accepting mock-only API
  shapes; treating null as proof; silently allowing the analytics beacon; enabling
  Sending for a smoke check or treating verified destinations as send permission.

## 6. Tests Required

Run `pytest tests/cloudflare_mail/test_mail_deploy.py`, shared backend quality gates,
strict mypy with `--explicit-package-bases`, PowerShell AST/DACL checks, Node syntax
check and the real public harness. Use a new task-owned pytest basetemp each run.
Do not rebuild or rerun old bundle acceptance when immutable inputs are unchanged.

HTTP mocks cover network boundaries only: pagination, errors/redirects/timeouts,
secret redaction, normal provisioning, null log readback, explicit asset recovery,
and existing-Worker publication with version drift, conflicts, split traffic and
logging failures. Assert old reports remain byte-identical and no creation/secret
write is replayed. Verify final D1 counts/settings separately through the real API.

## 7. Wrong vs Correct

Wrong: upload and GET settings share identical field paths; unit tests prove real
Cloudflare configuration. Correct: retain current official contracts plus live
readback, model the actual endpoint/resource structure, and verify active versions.

Wrong: Worker observability is disabled, therefore browser analytics is disabled.
Correct: zone Web Analytics injects independently; check real Chromium requests,
configure its own control, and retain the failed run.

Wrong: recover any failure by rerunning `--apply`, or close the task by deleting
new resources. Correct: inspect outcome, select the narrow recovery phase, retain
resource IDs and failure evidence; deletion requires a separate concrete scope.
