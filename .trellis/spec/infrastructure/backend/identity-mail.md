# Local identity and private mail adapter contract

## Ownership and evidence boundary

`infra/identity` owns the Keycloak lab and the private SMTP/API adapter.
`tests/identity_provider` owns SMTP behavior tests and Chromium actions. Production
OIDC validation stays in `enterprise_doc_api.browser_auth.oidc.OidcClient`.

The lab runs real Keycloak, SMTP and HTTP. A labeled in-memory HTTP capture service
replaces Cloudflare. Passing the lab does not prove Cloudflare delivery, Worker/D1,
public DNS, product HTTP sessions, invitations, customer acceptance or production
hosting. Keep the historical signed-fixture S5 evidence separate.

The separate `tests.first_use.keycloak_server` joins a real Keycloak instance to
the complete product journey. Its contract is
[integrated first use](../../foundation-tests/backend/first-use.md). That suite
does verify product sessions/invitations; its local mail capture still does not
prove Cloudflare sending or long-term identity hosting.

## Private adapter interface

`MailRelay.submit(raw, sender, recipients)` accepts one configured recipient and
the configured sender. It rejects invalid MIME and messages over 256 KiB before
network access. The Worker origin requires HTTPS without userinfo/path/query/fragment;
only the explicit test capture may use `http://capture:8080`.

The fixed `POST /external/api/send_mail` request uses `x-custom-auth` for the site
password and JSON `token` for the sender Address JWT. These are different from the
SMTP password, Cloudflare API token and Worker JWT signing secret. Never forward
the SMTP password or return upstream bodies to SMTP clients.

HTTP has a 10-second total deadline, 4 KiB response limit, no redirects and no
automatic retry. Accept only HTTP 200 with `{"status":"ok"}`. Fixed SMTP outcomes
are 250 (accepted), 550 (envelope), 552 (size), 554 (MIME), and 451 (provider failure).
Acceptance does not prove final delivery. AUTH and receiver limits are exercised
through real SMTP in the lab, including anonymous and wrong-password rejection.

The adapter disables protocol-library logs. The pinned upstream SMTP proxy logs
message bodies and omits site auth, so do not start it directly. The upstream
Worker still stores sent message bodies in D1 `sendbox`; identity links therefore
need the mailbox's access and retention policy. A log-free adapter does not make
the whole real mail path stateless.

## Native Cloudflare requirements

Pin release, Git commit and container digests; preserve only necessary source
excerpts and hashes. The v1.12.0 Worker uses `SEND_MAIL`, checks sender `DOMAINS`
and send eligibility before choosing `verifiedAddressList`, and retains a soft
quota guard. Keep the default send balance at zero and enable only the intended
sender. Do not infer Cloudflare verification from the project allowlist or OIDC's
`email_verified` claim.

Sending to account-verified destination addresses is free on all plans under the
currently checked official rules. Arbitrary recipients require Workers Paid.
Verify the actual account, address and sender onboarding before a real send;
the free Worker path is not evidence of free direct SMTP availability.

## Lab lifecycle and validation

Use a unique Compose project, pinned linux/amd64 images, loopback published ports
and ephemeral H2. On this Docker Desktop, an internal bridge did not publish host
ports; the working normal bridge is not a claim of blocked Internet egress.
Run a real loopback callback listener so browser navigation reaches a real endpoint.

`lab.compose.yaml` accepts optional `LAB_KEYCLOAK_HOST` (default `127.0.0.1`).
The product fixture sets `localhost` to keep IdP requests separate from host-only
product cookies on `127.0.0.1`; published interfaces stay loopback-only. Do not
reuse test issuer hostnames for a public deployment.

`KeycloakFixture` passes an allowlisted process environment to Docker. On Windows
keep `SYSTEMROOT`, `SYSTEMDRIVE`, `PROGRAMFILES`, `PROGRAMFILES(X86)`,
`PROGRAMW6432`, `COMMONPROGRAMFILES` and normal Docker/user path variables so
Compose plugins remain discoverable. Do not inherit unrelated Cloudflare/model
secrets or dump resolved Compose environments to evidence.

Generate credentials only in process memory/temporary container environments.
Do not save email bodies, action URLs, authorization codes, tokens, browser traces
or unredacted process errors. Screenshots omit the address bar and password values.
For Playwright Test, trace-off alone is insufficient: the real-product config also
sets `PLAYWRIGHT_NO_COPY_PROMPT=1` to suppress automatic aria failure snapshots
that may include Keycloak action URLs. Keep explicit screenshots before input.
Do not modify the product `.env`, database, existing containers or unrelated worktrees.

`run_validation` must close its callback listener and remove only its project
containers/network on success, failure and interruption. The report is passed
only after validation and cleanup succeed. Cleanup failure records a failed status
and sanitized diagnostic; a prior validation failure is also preserved. Do not
overwrite existing evidence. Images may remain as reusable dependencies.

Validate the adapter through its public interface with HTTP transport injection.
Run real Keycloak verification/reset, old-password rejection, link/code replay
rejection, the existing OIDC client and persisted `emailVerified=true`. The cleanup
regression injects a Docker process failure only after actual resource removal.

```powershell
& .\.venv\Scripts\python.exe -B -m pytest tests/identity_provider
& .\.venv\Scripts\python.exe -B -m mypy infra/identity --ignore-missing-imports
```

Also run the shared backend quality gates. No product Web changes are implied by
the lab browser helper. Account access errors and DNS network failures remain
explicit pending observations; they do not prove lack of Cloudflare support or NXDOMAIN.
