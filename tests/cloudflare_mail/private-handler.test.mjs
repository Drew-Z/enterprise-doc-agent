import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createPrivateMailHandler } from '../../infra/cloudflare_mail/private-handler.mjs';

test('missing private configuration rejects HTTP and email before upstream access', async () => {
  let fetchCalls = 0;
  let emailCalls = 0;
  let rejection;
  const handler = createPrivateMailHandler({
    fetch() { fetchCalls++; return new Response('upstream'); },
    email() { emailCalls++; },
  });
  const response = await handler.fetch(new Request('https://mail.example.test/'), {}, {});
  await handler.email({ setReject(reason) { rejection = reason; } }, {}, {});
  assert.deepEqual(
    { status: response.status, fetchCalls, emailCalls, rejection },
    { status: 503, fetchCalls: 0, emailCalls: 0, rejection: 'Mail service unavailable' },
  );
});

const validEnv = () => ({
  PASSWORDS: JSON.stringify(['s'.repeat(48)]),
  ADMIN_PASSWORDS: JSON.stringify(['a'.repeat(48)]),
  JWT_SECRET: 'j'.repeat(48),
  DISABLE_ADMIN_PASSWORD_CHECK: false,
});

for (const [name, patch] of [
  ['missing site password', { PASSWORDS: undefined }],
  ['missing admin password', { ADMIN_PASSWORDS: undefined }],
  ['missing signing key', { JWT_SECRET: undefined }],
  ['malformed JSON', { PASSWORDS: '[' }],
  ['empty array', { ADMIN_PASSWORDS: '[]' }],
  ['object instead of list', { PASSWORDS: '{}' }],
  ['null instead of list', { PASSWORDS: 'null' }],
  ['non-string item', { PASSWORDS: '[42]' }],
  ['short password', { PASSWORDS: '["short"]' }],
  ['whitespace in signing key', { JWT_SECRET: ' '.repeat(48) }],
  ['non-ASCII signing key', { JWT_SECRET: '密'.repeat(48) }],
  ['same secret across roles', { ADMIN_PASSWORDS: JSON.stringify(['s'.repeat(48)]) }],
  ['admin bypass enabled', { DISABLE_ADMIN_PASSWORD_CHECK: true }],
]) {
  test(name + ' fails closed for both handlers', async () => {
    let calls = 0;
    let rejected = false;
    const handler = createPrivateMailHandler({
      fetch() { calls++; return new Response('upstream'); },
      email() { calls++; },
    });
    const env = { ...validEnv(), ...patch };
    const result = await handler.fetch(new Request('https://mail.example.test/assets/app.js'), env, {});
    await handler.email({ setReject() { rejected = true; } }, env, {});
    assert.deepEqual({ status: result.status, calls, rejected }, { status: 503, calls: 0, rejected: true });
    assert.equal(await result.text(), 'Mail service unavailable');
    assert.equal(result.headers.get('cache-control'), 'no-store');
  });
}

test('valid independent secrets preserve upstream request, message and execution context', async () => {
  const env = validEnv();
  const request = new Request('https://mail.example.test/api/mails');
  const message = { setReject() { assert.fail('valid configuration rejected'); } };
  const ctx = {};
  let emailAccepted = false;
  const handler = createPrivateMailHandler({
    fetch(req, bindings, context) {
      assert.equal(req, request);
      assert.equal(bindings, env);
      assert.equal(context, ctx);
      return new Response('upstream auth result', { status: 401 });
    },
    email(mail, bindings, context) {
      assert.equal(mail, message);
      assert.equal(bindings, env);
      assert.equal(context, ctx);
      emailAccepted = true;
    },
  });
  const response = await handler.fetch(request, env, ctx);
  await handler.email(message, env, ctx);
  assert.equal(response.status, 401);
  assert.equal(await response.text(), 'upstream auth result');
  assert.equal(emailAccepted, true);
});

test('upstream failures are fixed responses without exception or response-body disclosure', async () => {
  let rejected;
  const broken = createPrivateMailHandler({
    fetch() { return new Response('SYNTHETIC_PRIVATE_DIAGNOSTIC', { status: 500 }); },
    email() { throw new Error('SYNTHETIC_PRIVATE_DIAGNOSTIC'); },
  });
  const response = await broken.fetch(new Request('https://mail.example.test/api/mails'), validEnv(), {});
  assert.equal(response.status, 503);
  assert.equal(await response.text(), 'Mail service unavailable');
  await broken.email({ setReject(reason) { rejected = reason; } }, validEnv(), {});
  assert.equal(rejected, 'Mail service unavailable');
});
