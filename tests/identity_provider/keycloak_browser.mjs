// Browser-only Keycloak actions. Secrets and callback codes travel over process pipes.
import { createRequire } from 'node:module';
import path from 'node:path';
import fs from 'node:fs';

const require = createRequire(path.resolve('apps/web/package.json'));
const { chromium } = require('@playwright/test');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
let stage = 'launch';
let browser;
let activePage;

function callback(url) {
  const parsed = new URL(url);
  if (parsed.origin !== input.webOrigin || parsed.pathname !== '/auth/callback') return null;
  const code = parsed.searchParams.get('code');
  const state = parsed.searchParams.get('state');
  return code && state ? { code, state } : null;
}

async function mailboxLink() {
  for (let attempt = 0; attempt < 40; attempt++) {
    const response = await fetch(input.captureOrigin + '/messages', {
      headers: { 'x-capture-key': input.captureKey },
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error('capture_unavailable');
    const { messages } = await response.json();
    if (messages.length > input.messageOffset) {
      const mail = messages.at(-1);
      if (mail.to_mail !== input.email) throw new Error('wrong_recipient');
      for (const match of mail.content.matchAll(/href="([^"]+)"/g)) {
        const candidate = new URL(match[1].replaceAll('&amp;', '&'));
        if (candidate.origin === input.keycloakOrigin
            && candidate.pathname.includes('/login-actions/')) return candidate.href;
      }
      throw new Error('action_link_missing');
    }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error('capture_timeout');
}

async function newPage() {
  const context = await browser.newContext();
  return { context, page: await context.newPage() };
}

try {
  browser = await chromium.launch({ headless: true });
  const { context, page } = await newPage();
  activePage = page;
  page.setDefaultTimeout(15000);
  stage = 'open_login';
  await page.goto(input.authorizationUrl);
  await page.locator('#username').waitFor();
  if (input.action === 'verify') {
    await page.screenshot({ path: path.join(input.evidenceDir, 'keycloak-login.png') });
  }

  let link = null;
  let oldPasswordRejected = false;
  if (input.action === 'reset') {
    stage = 'request_password_reset';
    await page.getByRole('link', { name: /forgot password/i }).click();
    await page.locator('#username').fill(input.email);
    await page.locator('input[type="submit"], button[type="submit"]').click();
    link = await mailboxLink();
    stage = 'follow_password_reset';
    await page.goto(link);
    await page.locator('#password-new').waitFor();
    await page.screenshot({ path: path.join(input.evidenceDir, 'keycloak-password-reset.png') });
    await page.locator('#password-new').fill(input.newPassword);
    await page.locator('#password-confirm').fill(input.newPassword);
    await page.locator('input[type="submit"], button[type="submit"]').click();
  } else {
    stage = 'submit_password';
    await page.locator('#username').fill(input.email);
    await page.locator('#password').fill(input.password);
    await page.locator('#kc-login').click();
    if (input.action === 'verify') {
      stage = 'wait_verification_email';
      link = await mailboxLink();
      await page.screenshot({ path: path.join(input.evidenceDir, 'keycloak-awaiting-verification.png') });
      stage = 'follow_verification';
      await page.goto(link);
    } else if (input.action === 'login_after_reset') {
      stage = 'reject_old_password';
      await page.getByText(/invalid username or password/i).waitFor();
      oldPasswordRejected = callback(page.url()) === null;
      await page.locator('#password').fill(input.newPassword);
      await page.locator('#kc-login').click();
    }
  }

  stage = 'authorization_callback';
  await page.waitForURL(url => callback(url.href) !== null);
  const result = callback(page.url());
  let replayDidNotAuthorize = null;
  let replayStatus = null;
  if (link) {
    stage = 'action_link_replay';
    const replay = await newPage();
    const response = await replay.page.goto(link);
    await replay.page.waitForLoadState('domcontentloaded');
    replayDidNotAuthorize = callback(replay.page.url()) === null;
    replayStatus = response?.status() ?? null;
    if (await replay.page.locator('#password-new').count()) {
      throw new Error('replayed_reset_form');
    }
    await replay.context.close();
  }
  await context.close();
  process.stdout.write(JSON.stringify({
    ok: true, ...result, oldPasswordRejected, replayDidNotAuthorize, replayStatus,
  }));
} catch (error) {
  if (activePage && !activePage.isClosed()) {
    await activePage.screenshot({
      path: path.join(input.evidenceDir, 'keycloak-' + input.action + '-failure.png'),
    }).catch(() => {});
  }
  const networkCode = error.message?.match(/net::(ERR_[A-Z_]+)/)?.[1] ?? null;
  process.stdout.write(JSON.stringify({ ok: false, stage, errorType: error.name, networkCode }));
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
}
