import { readFile, writeFile, mkdir, stat } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { randomBytes, randomUUID } from 'node:crypto';
import net from 'node:net';

const { values } = parseArgs({ options: {
  bundle: { type: 'string' }, toolchain: { type: 'string' },
  'work-dir': { type: 'string' }, 'evidence-dir': { type: 'string' },
  'playwright-package': { type: 'string' },
} });
for (const key of ['bundle', 'toolchain', 'work-dir', 'evidence-dir', 'playwright-package']) {
  if (!values[key]) throw new Error('Missing local harness path option: ' + key);
}
const bundle = resolve(values.bundle);
const toolRoot = resolve(values.toolchain);
const work = resolve(values['work-dir']);
const evidence = resolve(values['evidence-dir']);
for (const directory of [work, evidence]) {
  if (await stat(directory).then(() => true, error => {
    if (error.code === 'ENOENT') return false;
    throw error;
  })) throw new Error('Local harness refuses to overwrite an existing directory');
}
await mkdir(work, { recursive: true });
await mkdir(evidence, { recursive: true });

// Filter before importing Wrangler or launching a browser; no cloud credentials enter either.
const keep = new Set([
  'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'USERPROFILE', 'APPDATA',
  'LOCALAPPDATA', 'TEMP', 'TMP', 'HOME', 'SYSTEMDRIVE', 'PROGRAMFILES',
  'PROGRAMFILES(X86)', 'PROGRAMDATA', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE',
  'PLAYWRIGHT_BROWSERS_PATH', 'HOMEDRIVE', 'HOMEPATH',
]);
for (const key of Object.keys(process.env)) {
  if (!keep.has(key.toUpperCase())) delete process.env[key];
}
Object.assign(process.env, {
  WRANGLER_SEND_METRICS: 'false', WRANGLER_LOG_PATH: join(work, 'wrangler-logs'),
  WRANGLER_LOG: 'none', CLOUDFLARE_LOAD_DEV_VARS_FROM_DOT_ENV: 'false', CI: '1',
});
process.chdir(work);
const requireWorker = createRequire(join(toolRoot, 'package.json'));
const { unstable_dev, getPlatformProxy } = await import(pathToFileURL(requireWorker.resolve('wrangler')).href);
const requireBrowser = createRequire(resolve(values['playwright-package']));
const { chromium } = requireBrowser('@playwright/test');
const privateVars = {
  PASSWORDS: JSON.stringify([randomBytes(48).toString('base64url')]),
  ADMIN_PASSWORDS: JSON.stringify([randomBytes(48).toString('base64url')]),
  JWT_SECRET: randomBytes(48).toString('base64url'),
};
const siteSecret = JSON.parse(privateVars.PASSWORDS)[0];
const adminSecret = JSON.parse(privateVars.ADMIN_PASSWORDS)[0];
const secrets = [siteSecret, adminSecret, privateVars.JWT_SECRET];
const report = {
  status: 'running', started_at: new Date().toISOString(), checks: [],
  boundary: 'local workerd + D1 SQLite + real Chromium',
  public_deployment_performed: false, real_email_sent: false,
  cloudflare_credentials_in_child: false, secrets_written_to_deployable_bundle: false,
  cleanup: { browser_closed: false, dev_stopped: false, proxy_disposed: false, ports_closed: false },
};
let proxy;
let dev;
let browser;
let stage = 'initialization';
const ports = [];

function check(name, condition, details = {}) {
  report.checks.push({ name, passed: Boolean(condition), ...details });
  if (!condition) {
    stage = name;
    throw new Error('Check failed: ' + name);
  }
}
async function request(base, pathname, options = {}) {
  const response = await fetch(base + pathname, { ...options, signal: AbortSignal.timeout(15000) });
  const text = await response.text();
  let data;
  try { data = JSON.parse(text); } catch { data = null; }
  return { status: response.status, text, data };
}
async function closeDev() {
  if (dev) { await dev.stop(); dev = undefined; }
}
async function closeProxy() {
  if (proxy) { await proxy.dispose(); proxy = undefined; }
}
async function portIsClosed(port) {
  return new Promise(resolvePort => {
    const socket = net.createConnection({ host: '127.0.0.1', port });
    socket.setTimeout(1000);
    socket.once('connect', () => { socket.destroy(); resolvePort(false); });
    socket.once('error', () => { socket.destroy(); resolvePort(true); });
    socket.once('timeout', () => { socket.destroy(); resolvePort(false); });
  });
}
function simulatedEmail(address, marker) {
  return [
    'From: Local Test <sender@example.test>', 'To: ' + address,
    'Subject: Local private mailbox ' + marker,
    'Date: Wed, 16 Sep 2026 03:00:00 +0000',
    'Message-ID: <' + randomUUID() + '@example.test>', 'MIME-Version: 1.0',
    'Content-Type: text/plain; charset=utf-8', '',
    'Synthetic local test only. 本地模拟邮件，不含真实用户资料。 ' + marker,
  ].join('\r\n');
}
try {
  const originalConfig = JSON.parse(await readFile(join(bundle, 'wrangler.jsonc'), 'utf8'));
  check('receive_only_config', !originalConfig.send_email && originalConfig.workers_dev === false
    && originalConfig.preview_urls === false && originalConfig.routes.length === 0
    && originalConfig.assets.run_worker_first === true
    && originalConfig.vars.DEFAULT_SEND_BALANCE === 0
    && originalConfig.d1_databases[0].remote === false);
  const config = structuredClone(originalConfig);
  delete config.account_id;
  config.name = 'docagent-mail-local-' + randomBytes(6).toString('hex');
  config.main = join(bundle, 'worker.js');
  config.assets.directory = join(bundle, 'assets');
  config.$schema = join(bundle, 'provenance/wrangler-config-schema.json');
  config.d1_databases[0].database_id = randomUUID();
  config.d1_databases[0].database_name = config.name;
  const configPath = join(work, 'wrangler.jsonc');
  await writeFile(configPath, JSON.stringify(config, null, 2));
  report.runtime_config = { local: true, d1_remote: false, compatibility_date: config.compatibility_date };
  const persistRoot = join(work, 'state');
  const proxyOptions = { configPath, envFiles: [], persist: { path: join(persistRoot, 'v3') }, remoteBindings: false };
  const devOptions = {
    config: configPath, ip: '127.0.0.1', port: 0, inspectorPort: 0,
    local: true, persist: true, persistTo: persistRoot, logLevel: 'none', inspect: false,
    experimental: { forceLocal: true, disableExperimentalWarning: true, disableDevRegistry: true,
      watch: false, showInteractiveDevSession: false, enableContainers: false },
  };
  stage = 'initialize_local_d1';
  proxy = await getPlatformProxy(proxyOptions);
  for (const sqlFile of ['schema.sql', 'private-settings.sql']) {
    const sql = await readFile(join(bundle, sqlFile), 'utf8');
    const statements = sql.split(';').map(part => part.trim()).filter(Boolean);
    const result = await proxy.env.DB.batch(statements.map(statement => proxy.env.DB.prepare(statement)));
    check(sqlFile + '_initialized', result.every(item => item.success));
  }
  await closeProxy();
  stage = 'missing_configuration_on_real_worker';
  dev = await unstable_dev(config.main, devOptions);
  ports.push(dev.port);
  report.local_ports = [...ports];
  let base = 'http://127.0.0.1:' + dev.port;
  const missing = await request(base, '/');
  check('real_worker_missing_config_returns_503', missing.status === 503 && missing.text === 'Mail service unavailable', { http: missing.status });
  const missingMail = await request(base, '/cdn-cgi/local/email?from=sender%40example.test&to=owner01%40mailtest.ciallobill.ccwu.cc', {
    method: 'POST', headers: { 'content-type': 'message/rfc822' }, body: simulatedEmail('owner01@mailtest.ciallobill.ccwu.cc', 'MISSING'),
  });
  report.missing_config_email_simulator_http = missingMail.status;
  check('real_worker_missing_config_rejects_email', /Mail service unavailable/.test(missingMail.text));
  await closeDev();
  stage = 'start_private_worker';
  dev = await unstable_dev(config.main, { ...devOptions, vars: privateVars });
  ports.push(dev.port);
  report.local_ports = [...ports];
  base = 'http://127.0.0.1:' + dev.port;
  const siteHeaders = { 'x-custom-auth': siteSecret, 'content-type': 'application/json' };
  const adminHeaders = { ...siteHeaders, 'x-admin-auth': adminSecret };
  const mailboxPath = '/api/mails?limit=20&offset=0';
  for (const [name, headers] of [
    ['site_password_required', {}], ['wrong_site_password_rejected', { 'x-custom-auth': 'wrong' }],
    ['address_credential_required', siteHeaders],
    ['wrong_address_credential_rejected', { ...siteHeaders, authorization: 'Bearer invalid' }],
  ]) {
    const response = await request(base, mailboxPath, { headers });
    check(name, response.status === 401, { http: response.status });
  }
  for (const [name, headers] of [
    ['admin_password_required', siteHeaders],
    ['wrong_admin_password_rejected', { ...siteHeaders, 'x-admin-auth': 'wrong' }],
  ]) {
    const response = await request(base, '/admin/address?limit=20&offset=0', { headers });
    check(name, response.status === 401, { http: response.status });
  }
  const open = await request(base, '/open_api/settings');
  check('private_site_and_disabled_features_advertised', open.status === 200 && open.data.needAuth === true
    && open.data.enableSendMail === false && open.data.enableUserCreateEmail === false
    && open.data.enableWebhook === false && open.data.enableAutoReply === false);
  const register = await request(base, '/user_api/register', { method: 'POST', headers: siteHeaders, body: '{}' });
  check('public_user_registration_disabled', register.status === 403, { http: register.status });
  const anonymousCreate = await request(base, '/api/new_address', { method: 'POST', headers: siteHeaders, body: JSON.stringify({ name: 'disallowed', domain: 'mailtest.ciallobill.ccwu.cc' }) });
  check('public_address_creation_disabled', anonymousCreate.status === 403, { http: anonymousCreate.status });
  const addresses = [];
  for (const name of ['owner01', 'member01']) {
    const created = await request(base, '/admin/new_address', { method: 'POST', headers: adminHeaders,
      body: JSON.stringify({ name, domain: 'mailtest.ciallobill.ccwu.cc', enablePrefix: false }) });
    check('admin_creates_' + name, created.status === 200 && typeof created.data?.jwt === 'string', { http: created.status });
    addresses.push(created.data);
    secrets.push(created.data.jwt);
  }
  const [owner, member] = addresses;
  const ownerHeaders = { ...siteHeaders, authorization: 'Bearer ' + owner.jwt };
  const memberHeaders = { ...siteHeaders, authorization: 'Bearer ' + member.jwt };
  const unknown = await request(base, '/cdn-cgi/local/email?from=sender%40example.test&to=unknown%40mailtest.ciallobill.ccwu.cc', {
    method: 'POST', headers: { 'content-type': 'message/rfc822' }, body: simulatedEmail('unknown@mailtest.ciallobill.ccwu.cc', 'UNKNOWN'),
  });
  check('unknown_mailbox_rejected', /Unknown address/.test(unknown.text), { http: unknown.status });
  for (const [address, marker] of [[owner.address, 'OWNER_ONLY'], [member.address, 'MEMBER_ONLY']]) {
    const received = await request(base, '/cdn-cgi/local/email?from=sender%40example.test&to=' + encodeURIComponent(address), {
      method: 'POST', headers: { 'content-type': 'message/rfc822' }, body: simulatedEmail(address, marker),
    });
    check('local_receive_' + marker, received.status === 200, { http: received.status });
  }
  const ownerMail = await request(base, mailboxPath, { headers: ownerHeaders });
  const memberMail = await request(base, mailboxPath, { headers: memberHeaders });
  check('owner_inbox_isolated', ownerMail.status === 200 && ownerMail.data.results.length === 1
    && ownerMail.data.results[0].raw.includes('OWNER_ONLY') && !ownerMail.text.includes('MEMBER_ONLY'));
  check('member_inbox_isolated', memberMail.status === 200 && memberMail.data.results.length === 1
    && memberMail.data.results[0].raw.includes('MEMBER_ONLY') && !memberMail.text.includes('OWNER_ONLY'));
  const crossRead = await request(base, '/api/mail/' + ownerMail.data.results[0].id, { headers: memberHeaders });
  check('member_cannot_fetch_owner_mail_by_id', crossRead.status === 200 && crossRead.data === null);
  const setting = await request(base, '/api/settings', { headers: ownerHeaders });
  check('initial_send_balance_zero', setting.status === 200 && setting.data.send_balance === 0);
  stage = 'browser_private_site_login';
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1365, height: 900 }, locale: 'zh-CN' });
  let externalRequests = 0;
  const externalOrigins = new Map();
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin === base || ['data:', 'blob:'].includes(url.protocol)) await route.continue();
    else {
      externalRequests++;
      externalOrigins.set(url.origin, (externalOrigins.get(url.origin) || 0) + 1);
      await route.abort();
    }
  });
  const page = await context.newPage();
  let browserErrors = 0;
  page.on('pageerror', () => { browserErrors++; });
  await page.goto(base + '/zh/', { waitUntil: 'networkidle' });
  const siteInput = page.locator('.n-dialog input[type="password"]');
  await siteInput.waitFor();
  await page.screenshot({ path: join(evidence, 'private-site-gate.png') });
  const siteLogin = page.waitForResponse(response => response.url().endsWith('/open_api/site_login'));
  const siteReload = page.waitForNavigation({ waitUntil: 'networkidle' });
  await siteInput.fill(siteSecret);
  await siteInput.press('Enter');
  check('browser_site_password_accepted', (await siteLogin).status() === 200);
  await siteReload;
  await page.locator('textarea').waitFor();
  await page.screenshot({ path: join(evidence, 'mailbox-credential-gate.png') });
  stage = 'browser_address_credential_login';
  await page.locator('textarea').fill(owner.jwt);
  const credentialLogin = page.waitForResponse(response => response.url().endsWith('/open_api/credential_login'));
  await page.locator('form').filter({ has: page.locator('textarea') }).getByRole('button').first().click();
  check('browser_address_credential_accepted', (await credentialLogin).status() === 200);
  await page.getByText('Local private mailbox OWNER_ONLY', { exact: false }).first().waitFor();
  check('browser_shows_only_owner_mail', !(await page.locator('body').innerText()).includes('MEMBER_ONLY'));
  await page.screenshot({ path: join(evidence, 'private-owner-inbox.png') });
  check('browser_has_no_uncaught_errors', browserErrors === 0, { count: browserErrors });
  check('browser_requires_no_external_requests', externalRequests === 0, {
    count: externalRequests, origins: Object.fromEntries(externalOrigins),
  });
  await browser.close(); browser = undefined;
  await closeDev();
  stage = 'persisted_local_d1_check';
  proxy = await getPlatformProxy(proxyOptions);
  const counts = await proxy.env.DB.prepare('SELECT (SELECT count(*) FROM raw_mails) AS mails, (SELECT count(*) FROM address) AS addresses, (SELECT count(*) FROM sendbox) AS sent').first();
  check('persisted_d1_has_only_expected_data', counts.mails === 2 && counts.addresses === 2 && counts.sent === 0, { counts });
  await closeProxy();
  report.status = 'validation_passed';
} catch (error) {
  report.status = 'failed';
  report.failed_stage = stage;
  report.error_name = error?.name || 'Error';
  let message = String(error?.message || 'Local validation failed');
  for (const secret of secrets) message = message.replaceAll(secret, '[REDACTED]');
  report.error_summary = message.slice(0, 350);
} finally {
  for (const [name, close] of [
    ['browser_closed', async () => { if (browser) { await browser.close(); browser = undefined; } }],
    ['dev_stopped', closeDev], ['proxy_disposed', closeProxy],
  ]) {
    try { await close(); report.cleanup[name] = true; }
    catch { report.cleanup[name] = false; report.status = 'failed'; report.cleanup_failed = true; }
  }
  report.cleanup.ports_closed = (await Promise.all(ports.map(portIsClosed))).every(Boolean);
  if (!report.cleanup.ports_closed) report.status = 'failed';
  if (report.status === 'validation_passed') report.status = 'passed';
  report.finished_at = new Date().toISOString();
  await writeFile(join(evidence, 'report.json'), JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
}
process.stdout.write(JSON.stringify({ status: report.status, checks: report.checks.length, failed_stage: report.failed_stage, cleanup: report.cleanup }) + '\n');
process.exitCode = report.status === 'passed' ? 0 : 1;
