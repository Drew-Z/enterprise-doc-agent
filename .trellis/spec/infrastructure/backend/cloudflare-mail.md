# Private Cloudflare mailbox package contract

## 1. Scope / Trigger

`infra/cloudflare_mail` owns the pinned build, private Worker entry and receive-only
configuration. `tests/cloudflare_mail` owns its behavior tests and real local
Worker/D1/Chromium acceptance. The upstream is `cloudflare_temp_email` v1.12.0 at
`39db6bad4377dfb3d1d67d71bd589597dff3c352`.

This package prepares the mailbox side of the [identity adapter](./identity-mail.md).
Local acceptance does not establish public DNS, real delivery, long-lived Keycloak
hosting, product HTTP sessions, customer validation or processing-region guarantees.

## 2. Signatures

```powershell
& .\.venv\Scripts\python.exe -B -m infra.cloudflare_mail.build --work-dir $buildRoot --output-dir $bundlePath
& node ./tests/cloudflare_mail/local-worker.mjs --bundle $bundlePath --toolchain (Join-Path $buildRoot 'upstream/worker') --work-dir $mailLabRoot --evidence-dir $mailEvidence --playwright-package (Join-Path $PWD 'apps/web/package.json')
```

The build accepts optional `--source-dir` (untouched selected upstream files) and
`--cache-dir` (a task-owned pnpm/Corepack cache). Work/output must be new, separate
directories. Never pass an existing evidence directory to the local harness.

`createPrivateMailHandler(upstream)` exports only `fetch(request, env, ctx)` and
`email(message, env, ctx)`. Upstream owns authentication and mailbox storage; no
scheduled handler is exposed.

Upstream HTTP contracts used by the harness:

- `POST /open_api/site_login`, then `POST /open_api/credential_login` for the browser.
- `POST /admin/new_address` with `{name, domain, enablePrefix:false}` returns
  `{address, jwt, ...}`; site and admin headers are required.
- `GET /api/mails?limit=20&offset=0` and `GET /api/mail/{id}` use site auth and
  `Authorization: Bearer <Address JWT>`.
- Local workerd injection: `POST /cdn-cgi/local/email?from=...&to=...`,
  `Content-Type: message/rfc822`, with a synthetic RFC 5322 body and `Message-ID`.

## 3. Contracts

`PASSWORDS` and `ADMIN_PASSWORDS` are JSON strings encoding nonempty string arrays;
`JWT_SECRET` is a string. Every key is at least 32 non-whitespace ASCII characters.
Use independently generated random values; no value may repeat within or across
the site/admin/signing set. `DISABLE_ADMIN_PASSWORD_CHECK` accepts only undefined,
boolean `false`, or string `"false"`. All other values close the service.

Wrangler `secrets.required` contains names only; it does not enforce fail-closed
runtime behavior. The wrapper provides that behavior. API Token, site password,
admin password, SMTP password, Address JWT and signing secret are distinct values.

The initial package has `workers_dev=false`, `preview_urls=false`, `routes=[]`,
`assets.run_worker_first=true`, no cron or sending/forwarding binding, default send
balance zero, and a zero UUID for the future dedicated D1. Secrets never enter
`vars`, assets, source locks or the deployment ZIP. Public registration, address
creation, auto-reply, Webhooks, AI extraction and user delete are disabled.

Initialize only a new dedicated database with upstream `schema.sql`, followed by
`private-settings.sql`. The latter uses ordinary INSERT, so repeat initialization
fails. It disables user registration and sets
`email_rule_settings.blockReceiveUnknowAddressEmail=true` (upstream spelling), with
empty verified/unlimited-send lists. Do not reuse an existing account database.

Build pins pnpm 10.10.0 and the two upstream lockfiles, installs with
`--frozen-lockfile --ignore-scripts`, and uses the locked Wrangler 4.129.0.
`source.lock.json` checks the selected source file set, sizes and SHA-256; archive
download also checks its fixed digest and size. Source links/junction entries are
rejected. The optional source directory is verified again after building.

Only the verified working copy of `frontend/index.html` loses its unconditional
Turnstile script, because this private package disables captcha. The match must be
unique; manifest records original and patched hashes. The original selected source
and dependency locks stay unchanged. PWA/analytics/Telegram are disabled; browser
login and the synthetic inbox must issue zero external requests.

The harness removes Cloudflare/product credentials before importing Wrangler,
launches workerd on loopback, uses `local=true`, `forceLocal=true`,
`remoteBindings=false`, and D1 `remote=false`. `unstable_dev.persistTo` points to
`state`, while `getPlatformProxy.persist.path` points to the same `state/v3` storage.
Only the proxy initializes/checks D1; actual HTTP and email run through workerd.

## 4. Validation & Error Matrix

| Condition | Observable result |
| --- | --- |
| Missing/invalid private configuration | HTTP 503, exact `Mail service unavailable`, `Cache-Control: no-store`; email `setReject` with same text; upstream not called |
| Upstream HTTP exception or status >=500 | Same fixed 503, without upstream body |
| Upstream email exception escapes | Fixed rejection; upstream internally swallowed/logged errors remain outside this wrapper's guarantee |
| Missing/wrong site, admin or Address JWT | Upstream HTTP 401 on the corresponding protected endpoint |
| Public registration/address creation | HTTP 403 |
| Mail to an unknown address | Rejected by the upstream receive rule |
| Member reads owner's mail ID | HTTP 200 with JSON `null`; no owner's mail body |
| Altered/missing/additional selected source, existing/nested build directories | Build fails before overwriting caller-owned data |
| Any acceptance or cleanup failure | Report `status=failed`; preserve the failing stage and sanitized diagnostic |

## 5. Good / Base / Bad Cases

- Good: independent secrets, dedicated local D1, admin-created owner/member
  addresses, one synthetic message each, isolated API/browser inboxes and zero sendbox rows.
- Base: no secrets have been configured yet; both HTTP and email stay closed.
- Bad: bypassing admin checks, reusing keys, adding `SEND_MAIL` just for a smoke
  test, replacing a real database ID in local tests, or assuming an account-verified
  address is authorized for a real test send.

## 6. Tests Required

Run `node --test tests/cloudflare_mail/private-handler.test.mjs`, the Python build
guard tests, locked Wrangler dry-run/types and the real local harness. Also run
shared backend quality gates. When checking the namespace package together with
its Python tests, use `mypy --explicit-package-bases infra/cloudflare_mail
tests/cloudflare_mail/test_mail_package_build.py`.

Assert missing/invalid key rejection and upstream error redaction; altered source
and directory protection; child credential filtering; real site/admin/address
authorization; disabled public creation; unknown-address rejection; both inboxes
and cross-ID isolation; real browser login; zero external requests/page errors;
persisted counts of two addresses, two messages and zero sends.

Browser credentials stay out of URLs, screenshots, traces and video. Capture only
empty gates and the synthetic inbox. Preserve failed reports rather than overwrite
them. Close browser/dev/proxy and check listener ports in `finally`; only then mark
acceptance passed. Record and remove task-owned temporary source, caches and D1
after retaining final artifacts and necessary evidence.

## 7. Wrong vs Correct

Wrong: a successful `wrangler deploy --dry-run` proves the Worker starts locally;
use today's compatibility date regardless of the pinned runtime.

Correct: the locked runtime explicitly rejected `2026-09-16` and reported a latest
supported date of `2026-09-10`. Pin the package to `2026-09-10`, run real workerd
acceptance and upgrade date/runtime together. Dry-run never means a public upload.

Wrong: disabling persistent Worker logs makes identity mail stateless.

Correct: upstream stores message bodies in D1 and has internal exception logs.
Define retention and region requirements separately; do not enable Tail/debug
logging while handling real identity links. Public deployment and actual delivery
remain distinct acceptance stages.
