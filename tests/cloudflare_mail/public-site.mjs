// Real HTTPS/API/Chromium acceptance. No email is sent or injected.
import { readFile, writeFile, mkdir, stat } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { resolve, join } from 'node:path';
import { spawnSync } from 'node:child_process';

const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(process.argv[index], process.argv[index + 1]);
}
for (const required of ['--repo', '--credentials', '--mailboxes', '--evidence']) {
  if (!args.get(required)) throw new Error(`Missing ${required}`);
}
const repo = resolve(args.get('--repo'));
const credentialsPath = resolve(args.get('--credentials'));
const mailboxesPath = resolve(args.get('--mailboxes'));
const evidence = resolve(args.get('--evidence'));
const base = 'https://inbox.ciallobill.ccwu.cc';
const require = createRequire(join(repo, 'apps/web/package.json'));
const { chromium } = require('@playwright/test');
const shell = 'C:\\Users\\zhang\\AppData\\Local\\Microsoft\\WindowsApps\\pwsh.exe';
if (process.platform !== 'win32') throw new Error('This acceptance runner uses the Windows credential contract');
for (const path of [credentialsPath, mailboxesPath]) {
  const result = spawnSync(shell, ['-NoProfile', '-NonInteractive', '-File',
    join(repo, 'infra/cloudflare_mail/new-credentials.ps1'), '-CheckOnly', '-Path', path],
  { stdio: 'ignore', timeout: 20000, windowsHide: true });
  if (result.status !== 0) throw new Error('Private credential DACL check failed');
}
try { await stat(evidence); throw new Error('Evidence path exists'); }
catch (error) { if (error.code !== 'ENOENT') throw error; }
await mkdir(evidence);
const privateValues = JSON.parse(await readFile(credentialsPath, 'utf8'));
const siteSecret = JSON.parse(privateValues.PASSWORDS)[0];
const adminSecret = JSON.parse(privateValues.ADMIN_PASSWORDS)[0];
const mailboxes = JSON.parse(await readFile(mailboxesPath, 'utf8'));
const report = { started_at: new Date().toISOString(), origin: base, status: 'running',
  boundary: 'real_public_HTTPS_API_Chromium_no_mail_delivery', checks: [],
  real_email_sent: false, real_email_received_verified: false, browser_closed: false };
let stage = 'https';
let browser;

function check(name, passed, details = {}) {
  report.checks.push({ name, passed, ...details });
  if (!passed) throw new Error(name);
}

async function request(path, options = {}) {
  const response = await fetch(base + path, { ...options, redirect: 'error', signal: AbortSignal.timeout(30000) });
  const raw = await response.text();
  let data;
  try { data = JSON.parse(raw); } catch { data = null; }
  return { status: response.status, data };
}

try {
  const pageResponse = await request('/zh/');
  check('public_https_available', pageResponse.status === 200, { http: pageResponse.status });
  const siteHeaders = { 'x-custom-auth': siteSecret, 'content-type': 'application/json' };
  const adminHeaders = { ...siteHeaders, 'x-admin-auth': adminSecret };
  const mailboxPath = '/api/mails?limit=20&offset=0';
  stage = 'api_authentication';
  for (const [name, headers] of [
    ['site_password_required', {}],
    ['wrong_site_password_rejected', { 'x-custom-auth': 'wrong' }],
    ['address_credential_required', siteHeaders],
    ['wrong_address_credential_rejected', { ...siteHeaders, authorization: 'Bearer invalid' }],
  ]) {
    const response = await request(mailboxPath, { headers });
    check(name, response.status === 401, { http: response.status });
  }
  for (const [name, headers] of [
    ['admin_password_required', siteHeaders],
    ['wrong_admin_password_rejected', { ...siteHeaders, 'x-admin-auth': 'wrong' }],
  ]) {
    const response = await request('/admin/address?limit=20&offset=0', { headers });
    check(name, response.status === 401, { http: response.status });
  }
  const open = await request('/open_api/settings');
  check('private_receive_only_features', open.status === 200 && open.data?.needAuth === true
    && open.data.enableSendMail === false && open.data.enableUserCreateEmail === false
    && open.data.enableWebhook === false && open.data.enableAutoReply === false);
  const register = await request('/user_api/register', { method: 'POST', headers: siteHeaders, body: '{}' });
  check('public_user_registration_disabled', register.status === 403, { http: register.status });
  const create = await request('/api/new_address', { method: 'POST', headers: siteHeaders,
    body: JSON.stringify({ name: 'disallowed', domain: 'mailtest.ciallobill.ccwu.cc' }) });
  check('public_address_creation_disabled', create.status === 403, { http: create.status });
  stage = 'synthetic_mailboxes';
  for (const name of ['owner01', 'member01']) {
    const expectedAddress = name + '@mailtest.ciallobill.ccwu.cc';
    let created = false;
    if (!mailboxes[name]) {
      const response = await request('/admin/new_address', { method: 'POST', headers: adminHeaders,
        body: JSON.stringify({ name, domain: 'mailtest.ciallobill.ccwu.cc', enablePrefix: false }) });
      check('admin_creates_' + name, response.status === 200 && typeof response.data?.jwt === 'string'
        && response.data.address === expectedAddress, { http: response.status });
      mailboxes[name] = { address: response.data.address, jwt: response.data.jwt };
      // Write the existing protected file in place; retain its explicit DACL.
      await writeFile(mailboxesPath, JSON.stringify(mailboxes, null, 2));
      created = true;
    }
    const mailbox = mailboxes[name];
    check('expected_' + name + '_address', mailbox.address === expectedAddress
      && typeof mailbox.jwt === 'string', { created_this_run: created });
    const headers = { ...siteHeaders, authorization: 'Bearer ' + mailbox.jwt };
    const response = await request(mailboxPath, { headers });
    check(name + '_empty_authenticated_inbox', response.status === 200 && response.data?.results?.length === 0);
    const settings = await request('/api/settings', { headers });
    check(name + '_zero_send_balance', settings.status === 200 && settings.data?.send_balance === 0);
  }
  stage = 'browser_login';
  const allowed = new Set(['SYSTEMROOT', 'WINDIR', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE',
    'LOCALAPPDATA', 'APPDATA', 'SYSTEMDRIVE', 'PROGRAMDATA', 'COMSPEC', 'NUMBER_OF_PROCESSORS',
    'PROCESSOR_ARCHITECTURE', 'OS', 'PLAYWRIGHT_BROWSERS_PATH']);
  const browserEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => allowed.has(key.toUpperCase())));
  browser = await chromium.launch({ headless: true, env: browserEnv });
  const context = await browser.newContext({ viewport: { width: 1365, height: 900 }, locale: 'zh-CN' });
  let externalRequests = 0;
  const externalOrigins = new Set();
  let pageErrors = 0;
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin === base || ['data:', 'blob:'].includes(url.protocol)) await route.continue();
    else { externalRequests++; externalOrigins.add(url.origin); await route.abort(); }
  });
  const page = await context.newPage();
  page.on('pageerror', () => { pageErrors++; });
  await page.goto(base + '/zh/', { waitUntil: 'networkidle' });
  const siteInput = page.locator('.n-dialog input[type="password"]');
  await siteInput.waitFor();
  await page.screenshot({ path: join(evidence, 'private-site-gate.png') });
  const siteLogin = page.waitForResponse(response => response.url().endsWith('/open_api/site_login'));
  const reload = page.waitForNavigation({ waitUntil: 'networkidle' });
  await siteInput.fill(siteSecret);
  await siteInput.press('Enter');
  check('browser_site_login', (await siteLogin).status() === 200);
  await reload;
  await page.locator('textarea').waitFor();
  await page.screenshot({ path: join(evidence, 'mailbox-credential-gate.png') });
  const login = page.waitForResponse(response => response.url().endsWith('/open_api/credential_login'));
  await page.locator('textarea').fill(mailboxes.owner01.jwt);
  await page.locator('form').filter({ has: page.locator('textarea') }).getByRole('button').first().click();
  check('browser_mailbox_login', (await login).status() === 200);
  await page.locator('textarea').waitFor({ state: 'hidden' });
  await page.waitForLoadState('networkidle');
  const body = await page.locator('body').innerText();
  const visibleAddress = body.includes(mailboxes.owner01.address)
    || await page.locator('input').evaluateAll((inputs, address) => inputs.some(input => input.value === address), mailboxes.owner01.address);
  check('browser_owner_inbox_visible', visibleAddress && !body.includes(mailboxes.member01.address));
  await page.screenshot({ path: join(evidence, 'private-owner-empty-inbox.png') });
  check('browser_no_external_requests', externalRequests === 0,
    { count: externalRequests, origins: [...externalOrigins].sort() });
  check('browser_no_page_errors', pageErrors === 0, { count: pageErrors });
  report.status = 'checks_passed';
} catch (error) {
  report.status = 'failed';
  report.failed_stage = stage;
  report.error_name = error?.name || 'Error';
  // No raw response/Playwright error: it may contain private request payloads.
} finally {
  try { if (browser) await browser.close(); report.browser_closed = true; }
  catch { report.status = 'failed'; report.cleanup_error = 'browser_close_failed'; }
  if (report.status === 'checks_passed' && report.browser_closed) report.status = 'passed';
  report.finished_at = new Date().toISOString();
  await writeFile(join(evidence, 'report.json'), JSON.stringify(report, null, 2) + '\n');
}
console.log(JSON.stringify({ status: report.status, checks: report.checks.length, evidence }));
process.exitCode = report.status === 'passed' ? 0 : 1;
