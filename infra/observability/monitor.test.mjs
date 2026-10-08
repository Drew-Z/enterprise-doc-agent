import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';
import ts from 'typescript';
import { probeReadiness, probeRemoteBackup, recordObservation, deliverNotification, runScheduled } from './monitor.ts';

const now = Date.parse('2026-09-28T00:00:00Z');
const target = 'https://agent.example.test/health/ready';
const payload = () => ({
  status: 'ready',
  checked_at: new Date(now).toISOString(),
  checks: Object.fromEntries(['database', 'redis', 'object_store'].map((key) => [key, { status: 'up' }])),
});

test('required server queue observation cannot be replaced by healthy dependency checks', async () => {
  const result = await probeReadiness(target, {
    now: () => now, requireQueueObservation: true,
    fetch: async () => Response.json(payload()),
  });
  assert.deepEqual(result, { healthy: false, reason: 'queue_missing', checkedAt: null });
});

for (const [queue, reason] of [
  [null, 'queue_invalid'], [{ healthy: 'true', source_at: now }, 'queue_invalid'],
  [{ healthy: true, source_at: null }, 'queue_invalid'], [{ healthy: true, source_at: true }, 'queue_invalid'],
  [{ healthy: true, source_at: 0 }, 'queue_invalid'], [{ healthy: true, source_at: now + 15_001 }, 'queue_invalid'],
  [{ healthy: true, source_at: now - 45_001 }, 'queue_stale'],
  [{ healthy: false, source_at: null }, 'queue_unhealthy'],
  [{ healthy: false, source_at: now }, 'queue_unhealthy'],
  [{ healthy: true, source_at: now, private: 'PRIVATE_BODY' }, 'queue_invalid'],
  [{ healthy: true, source_at: now }, 'ready'],
]) {
  test(`server queue projection rejects invalid/stale sources: ${JSON.stringify(queue)}`, async () => {
    const result = await probeReadiness(target, { now: () => now, requireQueueObservation: true,
      fetch: async () => Response.json({ ...payload(), queue }) });
    assert.equal(result.reason, reason);
    assert.equal(JSON.stringify(result).includes('PRIVATE_BODY'), false);
  });
}

test('server queue failure/recovery preserves existing history and does not bypass another required heartbeat', async (t) => {
  const db = await database(t);
  const messages = [];
  const env = { ...mailEnv(db, async message => { messages.push(message); return { messageId: 'server-queue-event' }; }),
    DRILL_START_MS: '0', REQUIRE_QUEUE_OBSERVATION: 'true' };
  // An indeterminate earlier event remains indeterminate across migration.
  await recordObservation(db, sample(-1, true), 'observe');
  await db.prepare(`INSERT INTO notification_events (id, monitor_key, tick, kind, reason, delivery_status)
    VALUES ('older-unknown', ?, ?, 'failure', 'queue_stale', 'unknown')`).bind(env.MONITOR_KEY, now - 60_000).run();
  for (let minute = 0; minute < 6; minute++) {
    const tick = now + minute * 60_000;
    const boundary = { now: () => tick, fetch: async () => Response.json({ ...payload(), checked_at: new Date(tick).toISOString(),
      queue: { healthy: minute >= 3, source_at: tick } }) };
    await runScheduled(env, tick, boundary);
    await runScheduled(env, tick, { ...boundary, fetch: async () => assert.fail('duplicate probe') });
  }
  assert.equal(messages.length, 2);
  assert.equal((await db.prepare("SELECT delivery_status FROM notification_events WHERE id = 'older-unknown'").first()).delivery_status, 'unknown');
  const tick = now + 6 * 60_000;
  const result = await runScheduled({ ...env, QUEUE_HEARTBEAT_KEY: 'legacy-queue' }, tick, {
    now: () => tick, fetch: async () => Response.json({ ...payload(), checked_at: new Date(tick).toISOString(), queue: { healthy: true, source_at: tick } }),
  });
  assert.equal(result.healthy, false);
  assert.equal((await db.prepare('SELECT reason FROM monitor_state').first()).reason, 'queue_missing');
});

for (const stale of [false, true]) {
  test(`shipped workerd monitor requires server queue without any Windows heartbeat (stale: ${stale})`, async (t) => {
    const source = await readFile(new URL('./monitor.ts', import.meta.url), 'utf8');
    const script = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
    const mf = new Miniflare(convertV4MiniflareOptions({
      modules: true, compatibilityDate: '2026-09-26', script, unsafeTriggerHandlers: true,
      d1Databases: { MONITOR_DB: 'server-queue-runtime' },
      bindings: { MODE: 'observe', MONITOR_KEY: 'queue-runtime', TARGET_URL: target, DRILL_START_MS: '0', REQUIRE_QUEUE_OBSERVATION: 'true' },
      outboundService: () => Response.json({ ...payload(), checked_at: new Date().toISOString(),
        queue: { healthy: true, source_at: Date.now() - (stale ? 46_000 : 1000) } }),
    }));
    t.after(() => mf.dispose());
    const db = await mf.getD1Database('MONITOR_DB');
    await db.exec((await readFile(new URL('./schema.sql', import.meta.url), 'utf8')).replaceAll('\n', ' '));
    assert.equal((await mf.dispatchFetch('http://localhost/cdn-cgi/local/scheduled')).status, 200);
    const state = await db.prepare('SELECT reason, successes, failures FROM monitor_state').first();
    assert.deepEqual(state, stale ? { reason: 'queue_stale', successes: 0, failures: 1 } : { reason: 'ready', successes: 1, failures: 0 });
    assert.equal((await db.prepare('SELECT count(*) AS count FROM external_heartbeats').first()).count, 0);
    assert.equal((await mf.dispatchFetch('http://localhost/send')).status, 404);
  });
}

for (const [label, response, reason] of [
  ['redirect', () => new Response(null, { status: 302, headers: { Location: 'https://other.test/' } }), 'http_status'],
  ['HTML with status 200', () => new Response('private contents'), 'invalid_content_type'],
  ['missing dependency', () => Response.json({ ...payload(), checks: { database: { status: 'up' } } }), 'invalid_readiness'],
  ['unhealthy dependency', () => Response.json({ ...payload(), checks: { ...payload().checks, redis: { status: 'down' } } }), 'dependency_unhealthy'],
  ['unhealthy extra check', () => Response.json({ ...payload(), checks: { ...payload().checks, extra: { status: 'timeout' } } }), 'dependency_unhealthy'],
  ['stale body', () => Response.json({ ...payload(), checked_at: new Date(now - 120_001).toISOString() }), 'stale_readiness'],
  ['future body', () => Response.json({ ...payload(), checked_at: new Date(now + 15_001).toISOString() }), 'clock_skew'],
  ['timezone absent', () => Response.json({ ...payload(), checked_at: '2026-09-28T00:00:00' }), 'invalid_readiness'],
  ['JSON invalid', () => new Response('{', { headers: { 'Content-Type': 'application/json' } }), 'invalid_readiness'],
  ['oversize body', () => Response.json({ ...payload(), secret: 'x'.repeat(65_536) }), 'response_too_large'],
]) {
  test(`probe rejects ${label} without storing response contents`, async () => {
    const result = await probeReadiness(target, { now: () => now, fetch: async () => response() });
    assert.equal(result.healthy, false);
    assert.equal(result.reason, reason);
    assert.equal(JSON.stringify(result).includes('private contents'), false);
  });
}

test('probe deadline also bounds a body stalled after response headers', async () => {
  const result = await probeReadiness(target, {
    now: () => now, timeoutMs: 10,
    fetch: async () => new Response(new ReadableStream(), { headers: { 'Content-Type': 'application/json' } }),
  });
  assert.equal(result.reason, 'timeout');
});

test('network failures are redacted and the probe does not retry', async () => {
  let calls = 0;
  const result = await probeReadiness(target, {
    now: () => now,
    fetch: async () => { calls++; throw new Error('PRIVATE_NETWORK_DIAGNOSTIC'); },
  });
  assert.equal(calls, 1);
  assert.deepEqual(result, { healthy: false, reason: 'transport_error', checkedAt: null });
});

async function database(t) {
  const mf = new Miniflare(convertV4MiniflareOptions({
    modules: true, compatibilityDate: '2026-09-26',
    script: 'export default { fetch() { return new Response("test"); } };',
    d1Databases: { MONITOR_DB: 'monitor-test' },
  }));
  t.after(() => mf.dispose());
  const db = await mf.getD1Database('MONITOR_DB');
  const schema = await readFile(new URL('./schema.sql', import.meta.url), 'utf8');
  await db.exec(schema.replaceAll('\n', ' '));
  return db;
}

const sample = (minute, healthy, key = 'readiness-test') => ({
  key, tick: now + minute * 60_000, observedAt: now + minute * 60_000 + 10,
  probe: { healthy, reason: healthy ? 'ready' : 'http_status', checkedAt: null },
});

test('three failed minutes open one incident; two healthy minutes close it exactly once', async (t) => {
  const db = await database(t);
  assert.equal(await recordObservation(db, sample(0, false), 'notify'), null);
  assert.equal(await recordObservation(db, sample(1, false), 'notify'), null);
  const failure = await recordObservation(db, sample(2, false), 'notify');
  assert.equal(failure.kind, 'failure');
  assert.equal(await recordObservation(db, sample(3, false), 'notify'), null);
  assert.equal(await recordObservation(db, sample(4, true), 'notify'), null);
  const recovery = await recordObservation(db, sample(5, true), 'notify');
  assert.equal(recovery.kind, 'recovery');
  assert.equal(await recordObservation(db, sample(6, true), 'notify'), null);
  const events = await db.prepare('SELECT kind FROM notification_events ORDER BY tick').all();
  assert.deepEqual(events.results.map((item) => item.kind), ['failure', 'recovery']);
});

test('duplicate and late minutes cannot accumulate failures or race two incident events', async (t) => {
  const db = await database(t);
  await Promise.all(Array.from({ length: 4 }, () => recordObservation(db, sample(0, false), 'notify')));
  await recordObservation(db, sample(1, false), 'notify');
  const events = await Promise.all(Array.from({ length: 4 }, () => recordObservation(db, sample(2, false), 'notify')));
  assert.equal(events.filter(Boolean).length, 1);
  await recordObservation(db, sample(1, true), 'notify');
  const state = await db.prepare('SELECT * FROM monitor_state').first();
  assert.equal(state.failures, 3);
  assert.equal(state.successes, 0);
  assert.equal(state.last_tick, sample(2, false).tick);
});

test('missing minute resets the streak and expired or future observations are not evidence', async (t) => {
  const db = await database(t);
  await recordObservation(db, sample(0, false), 'notify');
  await recordObservation(db, sample(1, false), 'notify');
  assert.equal(await recordObservation(db, sample(3, false), 'notify'), null);
  assert.equal(await recordObservation(db, { ...sample(4, false), observedAt: sample(4, false).tick + 90_001 }, 'notify'), null);
  assert.equal(await recordObservation(db, { ...sample(4, false), observedAt: sample(4, false).tick - 1 }, 'notify'), null);
  const state = await db.prepare('SELECT * FROM monitor_state').first();
  assert.equal(state.failures, 1);
  assert.equal(state.last_tick, sample(3, false).tick);
});

function mailEnv(db, send) {
  return { MONITOR_DB: db, MAIL: { send }, MODE: 'notify', MONITOR_KEY: 'readiness-test',
    MAIL_FROM: 'ops@example.test', MAIL_TO: 'owner@example.test', TARGET_URL: target };
}

function clawEnv(db) {
  return { ...mailEnv(db, async () => assert.fail('must not fall back to the old binding')),
    MAIL_PROVIDER: 'clawemail', MAIL_FROM: 'monitor@claw.163.com', MAIL_TO: 'owner@example.test',
    CLAWEMAIL_API_KEY: 'ck_test_PRIVATE_KEY' };
}

function clawWire(env, override = () => undefined) {
  const calls = [];
  let attrs;
  const network = async (url, options) => {
    const parsed = new URL(url);
    assert.equal(parsed.origin, 'https://claw.163.com');
    assert.equal(options.method, 'POST');
    assert.equal(options.redirect, 'manual');
    const body = JSON.parse(options.body);
    const token = parsed.pathname.endsWith('/auth/token');
    const phase = token ? 'token' : body.action ?? 'receipt';
    calls.push({ phase, url, body, signal: options.signal });
    assert.equal(new Headers(options.headers).get('Authorization'),
      'Bearer ' + (token ? env.CLAWEMAIL_API_KEY : 'PRIVATE_ACCESS_TOKEN'));
    if (token) assert.deepEqual(body, { uid: env.MAIL_FROM });
    else {
      assert.equal(parsed.pathname, '/claw-api-gateway/api/coremail/proxy');
      assert.equal(parsed.searchParams.get('uid'), env.MAIL_FROM);
      assert.equal(parsed.searchParams.get('func'), phase === 'receipt' ? 'mbox:searchMessages' : 'mbox:compose');
    }
    const replacement = await override(phase, body, options);
    if (replacement !== undefined) return replacement;
    if (token) return Response.json({ result: { accessToken: 'PRIVATE_ACCESS_TOKEN', expiresIn: 1800 } });
    if (phase === 'continue') {
      attrs = body.attrs;
      assert.deepEqual(attrs.to, [env.MAIL_TO]);
      assert.equal(attrs.cc, undefined);
      assert.equal(attrs.bcc, undefined);
      assert.equal(attrs.saveSentCopy, true);
      assert.equal(attrs.isHtml, false);
      return Response.json({ code: 'S_OK', var: 'compose-123' });
    }
    if (phase === 'deliver') {
      assert.equal(body.id, 'compose-123');
      assert.deepEqual(body.attrs, attrs);
      return Response.json({ code: 'S_OK', var: null });
    }
    assert.equal(body.fid, 3);
    assert.equal(body.limit, 2);
    assert.equal(body.windowSize, 2);
    assert.equal(body.conditions[0].operand, attrs.subject);
    return Response.json({ code: 'S_OK', var: [{ fid: 3, from: env.MAIL_FROM, to: env.MAIL_TO,
      subject: attrs.subject, hmid: '<real-receipt@claw.163.com>' }] });
  };
  return { calls, network };
}

test('ClawEmail concurrent delivery uses fixed identity and a real Sent Message-ID exactly once', async (t) => {
  const db = await database(t);
  const event = await incident(db);
  const env = clawEnv(db);
  const wire = clawWire(env);
  const results = await Promise.all(Array.from({ length: 4 }, () => deliverNotification(env, event.id, () => now, 10_000, wire.network)));
  assert.equal(results.filter(Boolean).length, 1);
  assert.deepEqual(wire.calls.map(x => x.phase), ['token', 'continue', 'deliver', 'receipt']);
  const attrs = wire.calls[1].body.attrs;
  assert.ok(attrs.subject.includes(event.id));
  assert.ok(attrs.content.includes(event.id));
  assert.equal(attrs.content.includes(new URL(target).hostname), false);
  assert.equal(attrs.content.includes('PRIVATE'), false);
  const saved = await db.prepare('SELECT * FROM notification_events').first();
  assert.equal(saved.delivery_status, 'accepted');
  assert.equal(saved.provider_message_id, '<real-receipt@claw.163.com>');
  assert.equal(await deliverNotification(env, event.id, () => now, 10_000, wire.network), false);
  assert.equal(wire.calls.length, 4);
});

for (const [phase, reply, code] of [
  ['token', () => new Response(null, { status: 302, headers: { Location: 'https://untrusted.test' } }), 'provider_error'],
  ['token', () => Response.json({ result: { accessToken: 'PRIVATE_ACCESS_TOKEN', expiresIn: 0 } }), 'provider_error'],
  ['token', () => Response.json({ result: { accessToken: 'x'.repeat(65_537), expiresIn: 1800 } }), 'provider_error'],
  ['continue', () => Response.json({ code: 'FA_FORBIDDEN', message: 'PRIVATE_PROVIDER_ERROR' }), 'CLAW_FA_FORBIDDEN'],
  ['continue', () => Response.json({ code: 'S_OK', var: {} }), 'provider_error'],
  ['deliver', () => Response.json({ code: 'ACCESS_TOKEN_EXPIRED' }), 'CLAW_ACCESS_TOKEN_EXPIRED'],
  ['deliver', () => { throw new Error('PRIVATE_PROVIDER_ERROR'); }, 'provider_error'],
  ['deliver', () => Response.json({ code: 'PRIVATE_PROVIDER_ERROR' }), 'provider_error'],
  ['receipt', () => Response.json({ code: 'S_OK', var: [] }), 'invalid_receipt'],
  ['receipt', () => Response.json({ code: 'S_OK', var: [{ hmid: 'compose-123' }] }), 'invalid_receipt'],
]) {
  test(`ClawEmail ${phase} ${code} remains unknown without refreshing or retrying`, async (t) => {
    const db = await database(t);
    const event = await incident(db);
    const env = clawEnv(db);
    const wire = clawWire(env, stage => stage === phase ? reply() : undefined);
    assert.equal(await deliverNotification(env, event.id, () => now, 10_000, wire.network), false);
    assert.equal(await deliverNotification(env, event.id, () => now, 10_000, wire.network), false);
    assert.deepEqual(wire.calls.map(x => x.phase), ['token', 'continue', 'deliver', 'receipt'].slice(0, ['token', 'continue', 'deliver', 'receipt'].indexOf(phase) + 1));
    const saved = await db.prepare('SELECT * FROM notification_events').first();
    assert.equal(saved.delivery_status, 'unknown');
    assert.equal(saved.provider_message_id, null);
    const diagnostic = await db.prepare('SELECT code FROM notification_diagnostics').first();
    assert.equal(diagnostic.code, code);
    assert.equal(JSON.stringify([saved, diagnostic]).includes('PRIVATE'), false);
  });
}

for (const phase of ['token', 'continue', 'deliver', 'receipt']) {
  test(`ClawEmail timeout during ${phase} aborts and a late reply cannot start another request`, async (t) => {
    const db = await database(t);
    const event = await incident(db);
    const env = clawEnv(db);
    let release;
    const wire = clawWire(env, stage => stage === phase ? new Promise(resolve => { release = resolve; }) : undefined);
    assert.equal(await deliverNotification(env, event.id, () => now, 20, wire.network), false);
    const count = wire.calls.length;
    assert.equal(wire.calls.at(-1).phase, phase);
    assert.equal(wire.calls.at(-1).signal.aborted, true);
    release(undefined);
    await new Promise(resolve => setTimeout(resolve, 25));
    assert.equal(wire.calls.length, count);
    assert.equal((await db.prepare('SELECT code FROM notification_diagnostics').first()).code, 'timeout');
    assert.equal(await deliverNotification(env, event.id, () => now, 20, wire.network), false);
    assert.equal(wire.calls.length, count);
  });
}

for (const mismatch of ['from', 'to', 'subject', 'fid', 'hmid', 'duplicate']) {
  test(`ClawEmail refuses a Sent receipt with ${mismatch} mismatch`, async (t) => {
    const db = await database(t);
    const event = await incident(db);
    const env = clawEnv(db);
    const wire = clawWire(env, (phase, body) => {
      if (phase !== 'receipt') return;
      const row = { fid: 3, from: env.MAIL_FROM, to: env.MAIL_TO,
        subject: body.conditions[0].operand, hmid: '<other@claw.163.com>' };
      if (mismatch !== 'duplicate') row[mismatch] = mismatch === 'fid' ? 1 : 'wrong';
      return Response.json({ code: 'S_OK', var: mismatch === 'duplicate' ? [row, row] : [row] });
    });
    assert.equal(await deliverNotification(env, event.id, () => now, 10_000, wire.network), false);
    assert.equal((await db.prepare('SELECT code FROM notification_diagnostics').first()).code, 'invalid_receipt');
    assert.equal(wire.calls.filter(x => x.phase === 'deliver').length, 1);
    assert.equal(await deliverNotification(env, event.id, () => now, 10_000, wire.network), false);
    assert.equal(wire.calls.length, 4);
  });
}

test('ClawEmail stages share one deadline instead of resetting the budget per request', async (t) => {
  const db = await database(t);
  const event = await incident(db);
  const env = clawEnv(db);
  const wire = clawWire(env, async () => { await new Promise(resolve => setTimeout(resolve, 40)); });
  assert.equal(await deliverNotification(env, event.id, () => now, 100, wire.network), false);
  await new Promise(resolve => setTimeout(resolve, 60));
  assert.ok(wire.calls.length <= 3);
  assert.equal(wire.calls.some(x => x.phase === 'receipt'), false);
  assert.equal((await db.prepare('SELECT code FROM notification_diagnostics').first()).code, 'timeout');
});

test('ClawEmail cancels a stalled response body and does not advance to compose', async (t) => {
  const db = await database(t);
  const event = await incident(db);
  const env = clawEnv(db);
  let cancelled = false;
  const wire = clawWire(env, () => new Response(new ReadableStream({ cancel() { cancelled = true; } }),
    { headers: { 'Content-Type': 'application/json' } }));
  assert.equal(await deliverNotification(env, event.id, () => now, 20, wire.network), false);
  assert.equal(cancelled, true);
  assert.equal(wire.calls.length, 1);
});

for (const change of [{ MAIL_PROVIDER: 'unsupported' }, { CLAWEMAIL_API_KEY: '' }, { MAIL_FROM: 'owner@example.test' }]) {
  test(`ClawEmail invalid configuration rejects before claiming: ${JSON.stringify(change)}`, async (t) => {
    const db = await database(t);
    const event = await incident(db);
    await assert.rejects(deliverNotification({ ...clawEnv(db), ...change }, event.id, () => now), /invalid_mail_configuration/);
    assert.equal((await db.prepare('SELECT delivery_status FROM notification_events').first()).delivery_status, 'pending');
  });
}

test('shipped workerd scheduled handler delivers ClawEmail and preserves historical unknown events', async (t) => {
  const env = { ...clawEnv(null), DRILL_START_MS: '0' };
  const wire = clawWire(env);
  const source = await readFile(new URL('./monitor.ts', import.meta.url), 'utf8');
  const script = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
  const bindings = Object.fromEntries(Object.entries(env).filter(([key]) => !['MAIL', 'MONITOR_DB'].includes(key)));
  const mf = new Miniflare(convertV4MiniflareOptions({
    modules: true, compatibilityDate: '2026-09-26', script, unsafeTriggerHandlers: true,
    d1Databases: { MONITOR_DB: 'claw-runtime' }, bindings,
    outboundService: async request => request.url === target ? new Response(null, { status: 503 }) :
      wire.network(request.url, { method: request.method, headers: request.headers, body: await request.text(), redirect: 'manual', signal: request.signal }),
  }));
  t.after(() => mf.dispose());
  const db = await mf.getD1Database('MONITOR_DB');
  await db.exec((await readFile(new URL('./schema.sql', import.meta.url), 'utf8')).replaceAll('\n', ' '));
  const tick = Math.floor(Date.now() / 60_000) * 60_000;
  await db.prepare('INSERT INTO monitor_state (monitor_key,last_tick,observed_at,failures) VALUES (?,?,?,2)').bind(env.MONITOR_KEY, tick - 60_000, tick - 60_000).run();
  await db.prepare("INSERT INTO notification_events (id,monitor_key,tick,kind,reason,delivery_status) VALUES ('older-unknown',?,?,'failure','http_status','unknown')").bind(env.MONITOR_KEY, tick - 120_000).run();
  assert.equal((await mf.dispatchFetch('http://localhost/cdn-cgi/local/scheduled')).status, 200);
  assert.equal((await mf.dispatchFetch('http://localhost/cdn-cgi/local/scheduled')).status, 200);
  assert.equal(wire.calls.filter(x => x.phase === 'deliver').length, 1);
  const rows = (await db.prepare('SELECT id,delivery_status,provider_message_id FROM notification_events ORDER BY tick').all()).results;
  assert.equal(rows[0].delivery_status, 'unknown');
  assert.equal(rows[1].delivery_status, 'accepted');
  assert.equal(rows[1].provider_message_id, '<real-receipt@claw.163.com>');
  assert.equal((await mf.dispatchFetch('http://localhost/send')).status, 404);
});

async function incident(db, mode = 'notify') {
  await recordObservation(db, sample(0, false), mode);
  await recordObservation(db, sample(1, false), mode);
  return recordObservation(db, sample(2, false), mode);
}

test('concurrent senders claim a notification once and use only fixed recipient and content', async (t) => {
  const db = await database(t);
  const event = await incident(db);
  const messages = [];
  const env = mailEnv(db, async (message) => { messages.push(message); return { messageId: 'test-message-1' }; });
  await Promise.all(Array.from({ length: 4 }, () => deliverNotification(env, event.id, () => now)));
  assert.equal(messages.length, 1);
  assert.equal(messages[0].to, env.MAIL_TO);
  assert.equal(messages[0].from, env.MAIL_FROM);
  assert.equal(messages[0].cc, undefined);
  assert.equal(messages[0].bcc, undefined);
  assert.match(messages[0].text, /http_status/);
  const saved = await db.prepare('SELECT * FROM notification_events').first();
  assert.equal(saved.delivery_status, 'accepted');
  assert.equal(saved.provider_message_id, 'test-message-1');
  assert.equal(await deliverNotification(env, event.id, () => now), false);
});

for (const failure of ['exception', 'timeout', 'quota', 'invalid_receipt', 'untrusted_code']) {
  test(`send ${failure} stays unknown and cannot be automatically retried`, async (t) => {
    const db = await database(t);
    const event = await incident(db);
    let calls = 0;
    const env = mailEnv(db, async () => {
      calls++;
      if (failure === 'exception') throw new Error('PRIVATE_MAIL_ERROR');
      if (failure === 'quota') throw Object.assign(new Error('PRIVATE_MAIL_ERROR'), { code: 'E_DAILY_LIMIT_EXCEEDED' });
      if (failure === 'untrusted_code') throw Object.assign(new Error('PRIVATE_MAIL_ERROR'), { code: 'PRIVATE_DIAGNOSTIC' });
      if (failure === 'invalid_receipt') return undefined;
      return new Promise(() => {});
    });
    await deliverNotification(env, event.id, () => now, 10);
    assert.equal(await deliverNotification(env, event.id, () => now), false);
    assert.equal(calls, 1);
    const saved = await db.prepare('SELECT * FROM notification_events').first();
    assert.equal(saved.delivery_status, 'unknown');
    assert.equal(JSON.stringify(saved).includes('PRIVATE_MAIL_ERROR'), false);
    const diagnostic = await db.prepare('SELECT code FROM notification_diagnostics WHERE event_id = ?').bind(event.id).first();
    assert.equal(diagnostic.code, {
      exception: 'provider_error', timeout: 'timeout', quota: 'E_DAILY_LIMIT_EXCEEDED',
      invalid_receipt: 'invalid_receipt', untrusted_code: 'provider_error',
    }[failure]);
  });
}

test('observe mode and another monitor key never dispatch email', async (t) => {
  const db = await database(t);
  const event = await incident(db, 'observe');
  const env = mailEnv(db, async () => assert.fail('email must not be called'));
  assert.equal(event.delivery_status, 'suppressed');
  assert.equal(await deliverNotification(env, event.id, () => now), false);
  const other = await recordObservation(db, sample(0, false, 'drill-test'), 'notify');
  assert.equal(other, null);
  await recordObservation(db, sample(1, false, 'drill-test'), 'notify');
  const otherEvent = await recordObservation(db, sample(2, false, 'drill-test'), 'notify');
  assert.equal(await deliverNotification(env, otherEvent.id, () => now), false);
});

test('scheduled monitoring fetches the real target and suppresses repeated or stale invocations', async (t) => {
  const db = await database(t);
  const env = { ...mailEnv(db, async () => assert.fail('observe mode')), MODE: 'observe', DRILL_START_MS: '0' };
  let requests = 0;
  const boundary = { now: () => now + 10, fetch: async () => { requests++; return Response.json(payload()); } };
  assert.equal((await runScheduled(env, now, boundary)).status, 'sampled');
  assert.equal((await runScheduled(env, now, boundary)).status, 'ignored');
  assert.equal((await runScheduled(env, now - 120_000, boundary)).status, 'ignored');
  assert.equal((await runScheduled(env, now + 60_000, boundary)).status, 'ignored');
  assert.equal(requests, 1);
});

test('a five-minute drill produces exactly two labelled messages with isolated state and no production request', async (t) => {
  const db = await database(t);
  await recordObservation(db, sample(0, true), 'observe');
  const messages = [];
  const env = {
    ...mailEnv(db, async (message) => { messages.push(message); return { messageId: 'test-drill' }; }),
    MONITOR_KEY: 'drill-acceptance', DRILL_START_MS: String(now),
  };
  for (let minute = 0; minute < 7; minute++) {
    const tick = now + minute * 60_000;
    const boundary = { now: () => tick + 10, fetch: async () => assert.fail('drill must not call the service') };
    await runScheduled(env, tick, boundary);
    await runScheduled(env, tick, boundary);
  }
  assert.equal(messages.length, 2);
  assert.ok(messages.every((message) => message.subject.includes('[TEST]')));
  const production = await db.prepare('SELECT * FROM monitor_state WHERE monitor_key = ?').bind('readiness-test').first();
  assert.equal(production.last_tick, now);
  assert.equal(production.incident_open, 0);
});

test('a committed pending event survives interruption before dispatch and is sent by the next tick once', async (t) => {
  const db = await database(t);
  const event = await incident(db);
  const messages = [];
  const env = { ...mailEnv(db, async (message) => { messages.push(message); return { messageId: 'delayed-ack' }; }), DRILL_START_MS: '0' };
  const tick = now + 3 * 60_000;
  const boundary = { now: () => tick + 10, fetch: async () => new Response(null, { status: 503 }) };
  await runScheduled(env, tick, boundary);
  await runScheduled(env, tick, boundary);
  assert.equal(messages.length, 1);
  assert.ok(messages[0].text.includes(event.id));
});

test('the shipped Worker executes a scheduled readiness probe with real workerd and D1', async (t) => {
  const source = await readFile(new URL('./monitor.ts', import.meta.url), 'utf8');
  const script = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
  let requests = 0;
  const mf = new Miniflare(convertV4MiniflareOptions({
    modules: true, compatibilityDate: '2026-09-26', script,
    unsafeTriggerHandlers: true, d1Databases: { MONITOR_DB: 'worker-smoke' },
    bindings: { MODE: 'observe', MONITOR_KEY: 'readiness-runtime', TARGET_URL: target, DRILL_START_MS: '0',
      MAIL_FROM: 'ops@example.test', MAIL_TO: 'owner@example.test' },
    outboundService: () => {
      requests++;
      return Response.json({ ...payload(), checked_at: new Date().toISOString() });
    },
  }));
  t.after(() => mf.dispose());
  const db = await mf.getD1Database('MONITOR_DB');
  await db.exec((await readFile(new URL('./schema.sql', import.meta.url), 'utf8')).replaceAll('\n', ' '));
  const response = await mf.dispatchFetch('http://localhost/cdn-cgi/local/scheduled');
  assert.equal(response.status, 200);
  const state = await db.prepare('SELECT * FROM monitor_state').first();
  assert.equal(requests, 1);
  assert.equal(state.reason, 'ready');
  assert.equal(state.successes, 1);
  const noAdmin = await mf.dispatchFetch('http://localhost/send');
  assert.equal(noAdmin.status, 404);
});

test('external probe accepts only a fresh complete readiness response', async () => {
  let calls = 0;
  const result = await probeReadiness(target, {
    now: () => now,
    fetch: async (url, options) => {
      calls++;
      assert.equal(url, target);
      assert.equal(options.redirect, 'manual');
      assert.equal(options.headers.Accept, 'application/json');
      assert.equal(options.headers.Authorization, undefined);
      return Response.json(payload());
    },
  });
  assert.equal(calls, 1);
  assert.equal(result.healthy, true);
  assert.equal(result.reason, 'ready');
  assert.equal(result.checkedAt, now);
});

test('a stale backup cannot be hidden by healthy public readiness or a fresh delivery heartbeat', async (t) => {
  const db = await database(t);
  await db.exec('CREATE TABLE IF NOT EXISTS external_heartbeats (source_key TEXT PRIMARY KEY, source_at INTEGER NOT NULL, observed_at INTEGER NOT NULL, healthy INTEGER NOT NULL CHECK (healthy IN (0,1)), artifact_sha256 TEXT);');
  await db.prepare('INSERT INTO external_heartbeats VALUES (?, ?, ?, ?, ?)').bind('backup-test', now - 300_001, now, 1, 'a'.repeat(64)).run();
  const env = { ...mailEnv(db, async () => assert.fail('observe only')), MODE: 'observe', DRILL_START_MS: '0', BACKUP_HEARTBEAT_KEY: 'backup-test' };
  const result = await runScheduled(env, now, { now: () => now + 10, fetch: async () => Response.json(payload()) });
  assert.equal(result.healthy, false);
  assert.equal((await db.prepare('SELECT reason FROM monitor_state').first()).reason, 'backup_stale');
});

for (const [label, row, reason] of [
  ['missing queue', null, 'queue_missing'],
  ['failed queue query', [now, now, 0], 'queue_unhealthy'],
  ['stale queue source', [now - 120_001, now, 1], 'queue_stale'],
  ['future queue clock', [now + 15_001, now + 15_001, 1], 'queue_invalid'],
]) {
  test(`operational monitor rejects ${label}`, async (t) => {
    const db = await database(t);
    if (row) await db.prepare('INSERT INTO external_heartbeats VALUES (?, ?, ?, ?, NULL)').bind('queue-test', ...row).run();
    const env = { ...mailEnv(db, async () => assert.fail('observe only')), MODE: 'observe', DRILL_START_MS: '0', QUEUE_HEARTBEAT_KEY: 'queue-test' };
    const result = await runScheduled(env, now, { now: () => now, fetch: async () => Response.json(payload()) });
    assert.equal(result.healthy, false);
    assert.equal((await db.prepare('SELECT reason FROM monitor_state').first()).reason, reason);
  });
}

test('backup completion without a verified artifact digest is unhealthy', async (t) => {
  const db = await database(t);
  await db.prepare('INSERT INTO external_heartbeats VALUES (?, ?, ?, 1, NULL)').bind('backup-test', now, now).run();
  const env = { ...mailEnv(db, async () => assert.fail('observe only')), MODE: 'observe', DRILL_START_MS: '0', BACKUP_HEARTBEAT_KEY: 'backup-test' };
  assert.equal((await runScheduled(env, now, { now: () => now, fetch: async () => Response.json(payload()) })).healthy, false);
  assert.equal((await db.prepare('SELECT reason FROM monitor_state').first()).reason, 'backup_unverified');
});

test('a separate watchdog detects stopped scheduling without probing or stopping the application', async (t) => {
  const db = await database(t);
  await recordObservation(db, sample(0, true, 'main-test'), 'observe');
  const env = { ...mailEnv(db, async () => assert.fail('observe only')), MODE: 'observe', DRILL_START_MS: '0', CHECK_READINESS: 'false', WATCH_MONITOR_KEY: 'main-test' };
  let tick = now + 180_000;
  const boundary = { now: () => tick + 20, fetch: async () => assert.fail('watchdog must not probe') };
  assert.equal((await runScheduled(env, tick, boundary)).healthy, false);
  assert.equal((await db.prepare('SELECT reason FROM monitor_state WHERE monitor_key = ?').bind(env.MONITOR_KEY).first()).reason, 'monitor_stale');
  tick += 60_000;
  await recordObservation(db, sample(4, false, 'main-test'), 'observe');
  assert.equal((await runScheduled(env, tick, boundary)).healthy, true);
});

test('watchdog cannot disable all observations or watch itself', async (t) => {
  const db = await database(t);
  const base = { ...mailEnv(db, async () => assert.fail('no send')), MODE: 'observe', DRILL_START_MS: '0', CHECK_READINESS: 'false' };
  const boundary = { now: () => now, fetch: async () => assert.fail('no network') };
  await assert.rejects(runScheduled(base, now, boundary), /invalid_operational_monitor_configuration/);
  await assert.rejects(runScheduled({ ...base, WATCH_MONITOR_KEY: base.MONITOR_KEY }, now, boundary), /invalid_operational_monitor_configuration/);
});

test('queue failure and recovery use the existing three/two streak and one-time delivery contract', async (t) => {
  const db = await database(t);
  const messages = [];
  const env = { ...mailEnv(db, async message => { messages.push(message); return { messageId: 'queue-test-message' }; }), DRILL_START_MS: '0', QUEUE_HEARTBEAT_KEY: 'queue-test' };
  for (let minute = 0; minute < 6; minute++) {
    const tick = now + minute * 60_000;
    await db.prepare('INSERT INTO external_heartbeats VALUES (?, ?, ?, ?, NULL) ON CONFLICT(source_key) DO UPDATE SET source_at=excluded.source_at, observed_at=excluded.observed_at, healthy=excluded.healthy').bind('queue-test', tick, tick, minute >= 3 ? 1 : 0).run();
    await runScheduled(env, tick, { now: () => tick, fetch: async () => Response.json({ ...payload(), checked_at: new Date(tick).toISOString() }) });
  }
  assert.equal(messages.length, 2);
  assert.match(messages[0].text, /queue_unhealthy/);
  const events = await db.prepare('SELECT kind, delivery_status FROM notification_events ORDER BY tick').all();
  assert.deepEqual(events.results, [{ kind: 'failure', delivery_status: 'accepted' }, { kind: 'recovery', delivery_status: 'accepted' }]);
});

for (const stale of [false, true]) {
  test(`the shipped Worker checks operational heartbeats in workerd (stale backup: ${stale})`, async (t) => {
    const source = await readFile(new URL('./monitor.ts', import.meta.url), 'utf8');
    const script = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
    const mf = new Miniflare(convertV4MiniflareOptions({
      modules: true, compatibilityDate: '2026-09-26', script, unsafeTriggerHandlers: true,
      d1Databases: { MONITOR_DB: 'operational-runtime' },
      bindings: { MODE: 'observe', MONITOR_KEY: 'ops-runtime', TARGET_URL: target, DRILL_START_MS: '0',
        QUEUE_HEARTBEAT_KEY: 'queue-runtime', BACKUP_HEARTBEAT_KEY: 'backup-runtime', WATCH_MONITOR_KEY: 'watchdog-runtime' },
      outboundService: () => Response.json({ ...payload(), checked_at: new Date().toISOString() }),
    }));
    t.after(() => mf.dispose());
    const db = await mf.getD1Database('MONITOR_DB');
    await db.exec((await readFile(new URL('./schema.sql', import.meta.url), 'utf8')).replaceAll('\n', ' '));
    const captured = Date.now();
    await db.prepare('INSERT INTO external_heartbeats VALUES (?, ?, ?, 1, NULL)').bind('queue-runtime', captured, captured).run();
    await db.prepare('INSERT INTO external_heartbeats VALUES (?, ?, ?, 1, ?)').bind('backup-runtime', captured - (stale ? 360_000 : 0), captured, 'a'.repeat(64)).run();
    await db.prepare('INSERT INTO monitor_state (monitor_key, observed_at) VALUES (?, ?)').bind('watchdog-runtime', captured).run();
    assert.equal((await mf.dispatchFetch('http://localhost/cdn-cgi/local/scheduled')).status, 200);
    const state = await db.prepare('SELECT reason, successes, failures FROM monitor_state WHERE monitor_key = ?').bind('ops-runtime').first();
    assert.deepEqual(state, stale ? { reason: 'backup_stale', successes: 0, failures: 1 } : { reason: 'ready', successes: 1, failures: 0 });
  });
}

const remotePrefix = 'operations-recovery/v1/';
const remoteMaxBytes = 3 * 1024 ** 3;

async function remoteBucket(t) {
  const mf = new Miniflare(convertV4MiniflareOptions({
    modules: true, compatibilityDate: '2026-09-26',
    script: 'export default { fetch() { return new Response("test"); } };',
    r2Buckets: { BACKUP_BUCKET: 'backup-test' },
  }));
  t.after(() => mf.dispose());
  return mf.getR2Bucket('BACKUP_BUCKET');
}

async function seedRemote(bucket, capturedAt, nonce = 1, overrides = {}) {
  const iso = new Date(capturedAt).toISOString();
  const date = iso.slice(0, 10).replaceAll('-', '');
  const time = iso.slice(11, 19).replaceAll(':', '');
  const id = `snapshot-${date}-${time}-${nonce.toString(16).padStart(12, '0')}`;
  const cipherKey = remotePrefix + id + '.tar.age';
  const markerKey = remotePrefix + id + '.complete.json';
  const marker = {
    schema_version: 1, snapshot_id: id, captured_at: iso,
    ciphertext_key: cipherKey, ciphertext_sha256: 'a'.repeat(64), ciphertext_bytes: 64,
    status: 'ciphertext_upload_readback_verified', actual_restore_verified: false,
    ...overrides,
  };
  // Synthetic storage fixture: upload-marker monitoring does not decrypt data.
  await bucket.put(cipherKey, new Uint8Array(64));
  await bucket.put(markerKey, JSON.stringify(marker));
  return { marker, markerKey, cipherKey };
}

test('remote upload health uses real R2 objects and never asserts actual restoration', async (t) => {
  const bucket = await remoteBucket(t);
  const captured = Date.now() - 1000;
  const fixture = await seedRemote(bucket, captured);
  const result = await probeRemoteBackup(bucket, { now: Date.now, maxBytes: remoteMaxBytes });
  assert.deepEqual(result, { healthy: true, reason: 'backup_upload_fresh', checkedAt: captured });
  assert.equal((await (await bucket.get(fixture.markerKey)).json()).actual_restore_verified, false);
});

test('a just-uploaded old snapshot remains stale and cannot refresh its source age', async (t) => {
  const bucket = await remoteBucket(t);
  await seedRemote(bucket, Date.now() - 360_000);
  const result = await probeRemoteBackup(bucket, { now: Date.now, maxBytes: remoteMaxBytes });
  assert.equal(result.reason, 'backup_remote_stale');
  assert.equal(result.healthy, false);
});

test('remote missing, incomplete, and capacity-limited backups fail without modifying R2', async (t) => {
  const bucket = await remoteBucket(t);
  assert.equal((await probeRemoteBackup(bucket, { now: Date.now, maxBytes: remoteMaxBytes })).reason, 'backup_remote_missing');
  const fixture = await seedRemote(bucket, Date.now() - 1000);
  const before = await bucket.list();
  assert.equal((await probeRemoteBackup(bucket, { now: Date.now, maxBytes: 1000 })).reason, 'backup_remote_capacity_low');
  assert.deepEqual((await bucket.list()).objects.map(x => x.etag), before.objects.map(x => x.etag));
  await bucket.delete(fixture.markerKey);
  assert.equal((await probeRemoteBackup(bucket, { now: Date.now, maxBytes: remoteMaxBytes })).reason, 'backup_remote_upload_pending');
});

for (const [label, override] of [
  ['wrong identity', { snapshot_id: 'snapshot-other' }],
  ['wrong object size', { ciphertext_bytes: 65 }],
  ['bad digest', { ciphertext_sha256: 'not-a-digest' }],
  ['invalid calendar', { captured_at: '2026-02-30T00:00:00Z' }],
  ['future source', { captured_at: new Date(Date.now() + 86_400_000).toISOString() }],
]) {
  test(`remote monitor rejects ${label}`, async (t) => {
    const bucket = await remoteBucket(t);
    await seedRemote(bucket, Date.now() - 1000, 1, override);
    assert.equal((await probeRemoteBackup(bucket, { now: Date.now, maxBytes: remoteMaxBytes })).reason, 'backup_remote_invalid');
  });
}

test('object deletion after listing and a changing completion marker cannot be healthy', async (t) => {
  const bucket = await remoteBucket(t);
  await seedRemote(bucket, Date.now() - 1000);
  const boundary = {
    list: options => bucket.list(options), get: key => bucket.get(key), head: async () => null,
  };
  assert.equal((await probeRemoteBackup(boundary, { now: Date.now, maxBytes: remoteMaxBytes })).reason, 'backup_remote_changed');
  boundary.get = async key => {
    const original = await bucket.get(key);
    return { ...original, etag: 'changed', body: original.body };
  };
  assert.equal((await probeRemoteBackup(boundary, { now: Date.now, maxBytes: remoteMaxBytes })).reason, 'backup_remote_changed');
});

test('remote pagination, request failure and stalled response are bounded and redacted', async (t) => {
  const bucket = await remoteBucket(t);
  await seedRemote(bucket, Date.now() - 1000);
  const boundary = { list: options => bucket.list(options), get: key => bucket.get(key), head: key => bucket.head(key) };
  boundary.list = async () => ({ objects: [], truncated: true, cursor: 'repeated' });
  assert.equal((await probeRemoteBackup(boundary, { now: Date.now, maxBytes: remoteMaxBytes })).reason, 'backup_remote_invalid');
  boundary.list = async () => { throw new Error('PRIVATE_R2_ERROR'); };
  assert.deepEqual(await probeRemoteBackup(boundary, { now: Date.now, maxBytes: remoteMaxBytes }),
    { healthy: false, reason: 'backup_remote_unavailable', checkedAt: null });
  boundary.list = async () => new Promise(() => {});
  assert.equal((await probeRemoteBackup(boundary, { now: Date.now, maxBytes: remoteMaxBytes, timeoutMs: 10 })).reason, 'backup_remote_timeout');
});

test('remote marker body timeout cancels its reader and prevents later object requests', async (t) => {
  const bucket = await remoteBucket(t);
  await seedRemote(bucket, Date.now() - 1000);
  const listing = await bucket.list({ prefix: remotePrefix });
  const metadata = listing.objects.find(item => item.key.endsWith('.complete.json'));
  let cancelled = false;
  let heads = 0;
  const boundary = {
    list: async () => listing,
    get: async () => ({ ...metadata, body: new ReadableStream({ cancel() { cancelled = true; } }) }),
    head: async () => { heads++; assert.fail('timed-out marker must not proceed to object head'); },
  };
  const result = await probeRemoteBackup(boundary, { now: Date.now, maxBytes: remoteMaxBytes, timeoutMs: 10 });
  assert.equal(result.reason, 'backup_remote_timeout');
  assert.equal(cancelled, true);
  assert.equal(heads, 0);
});

test('R2 upload failures and recovery keep the existing incident and delivery deduplication', async (t) => {
  const db = await database(t);
  const bucket = await remoteBucket(t);
  const start = Math.floor(Date.now() / 60_000) * 60_000;
  const messages = [];
  const env = { ...mailEnv(db, async message => { messages.push(message); return { messageId: 'backup-test-message' }; }),
    DRILL_START_MS: '0', BACKUP_REMOTE_PREFIX: remotePrefix, BACKUP_REMOTE_MAX_BYTES: String(remoteMaxBytes), BACKUP_BUCKET: bucket };
  for (let minute = 0; minute < 6; minute++) {
    const tick = start + minute * 60_000;
    if (minute >= 3) await seedRemote(bucket, start, minute);
    await runScheduled(env, tick, { now: () => tick, fetch: async () => Response.json({ ...payload(), checked_at: new Date(tick).toISOString() }) });
    await runScheduled(env, tick, { now: () => tick, fetch: async () => assert.fail('duplicate must not fetch') });
  }
  assert.equal(messages.length, 2);
  assert.match(messages[0].text, /backup_remote_missing/);
  const events = await db.prepare('SELECT kind, delivery_status FROM notification_events ORDER BY tick').all();
  assert.deepEqual(events.results, [{ kind: 'failure', delivery_status: 'accepted' }, { kind: 'recovery', delivery_status: 'accepted' }]);
});

for (const stale of [false, true]) {
  test(`the shipped Worker checks R2 completion markers through real workerd/D1 (stale: ${stale})`, async (t) => {
    const source = await readFile(new URL('./monitor.ts', import.meta.url), 'utf8');
    const script = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
    const mf = new Miniflare(convertV4MiniflareOptions({
      modules: true, compatibilityDate: '2026-09-26', script, unsafeTriggerHandlers: true,
      d1Databases: { MONITOR_DB: 'remote-backup-runtime' }, r2Buckets: { BACKUP_BUCKET: 'remote-backup-test' },
      bindings: { MODE: 'observe', MONITOR_KEY: 'backup-runtime', TARGET_URL: target, DRILL_START_MS: '0',
        BACKUP_REMOTE_PREFIX: remotePrefix, BACKUP_REMOTE_MAX_BYTES: String(remoteMaxBytes) },
      outboundService: () => Response.json({ ...payload(), checked_at: new Date().toISOString() }),
    }));
    t.after(() => mf.dispose());
    const db = await mf.getD1Database('MONITOR_DB');
    await db.exec((await readFile(new URL('./schema.sql', import.meta.url), 'utf8')).replaceAll('\n', ' '));
    const bucket = await mf.getR2Bucket('BACKUP_BUCKET');
    await seedRemote(bucket, Date.now() - (stale ? 360_000 : 1000));
    assert.equal((await mf.dispatchFetch('http://localhost/cdn-cgi/local/scheduled')).status, 200);
    const state = await db.prepare('SELECT reason, successes, failures FROM monitor_state').first();
    assert.deepEqual(state, stale ? { reason: 'backup_remote_stale', successes: 0, failures: 1 }
      : { reason: 'ready', successes: 1, failures: 0 });
    assert.equal((await mf.dispatchFetch('http://localhost/send')).status, 404);
  });
}
