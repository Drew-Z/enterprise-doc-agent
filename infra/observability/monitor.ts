// Keep the wire contract aligned with scripts/check_external_readiness.py.
const MINUTE = 60_000;
const MAX_RESPONSE_BYTES = 64 * 1024;

type Probe = { healthy: boolean; reason: string; checkedAt: number | null };
type Network = (url: string, options: RequestInit) => Promise<Response>;
type ProbeOptions = { fetch: Network; now: () => number; timeoutMs?: number };

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function validateTarget(value: string): void {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.search ||
      url.hash || url.pathname !== '/health/ready') {
    throw new Error('invalid_probe_target');
  }
}

async function deadline<T>(operation: Promise<T>, milliseconds: number, abort?: () => void): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      operation,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => {
          abort?.();
          reject(new Error('deadline_exceeded'));
        }, milliseconds);
      }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

async function responseBytes(
  response: Response, maximum = MAX_RESPONSE_BYTES,
  onCancel?: (cancel: () => void) => void,
): Promise<Uint8Array> {
  if (!response.body) throw new Error('invalid_readiness');
  const reader = response.body.getReader();
  onCancel?.(() => { void reader.cancel().catch(() => {}); });
  const buffer = new Uint8Array(maximum);
  let size = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) return buffer.subarray(0, size);
      const value: unknown = chunk.value;
      if (!(value instanceof Uint8Array)) throw new Error('invalid_readiness');
      if (size + value.byteLength > maximum) throw new Error('response_too_large');
      buffer.set(value, size);
      size += value.byteLength;
    }
  } finally {
    void reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export async function probeReadiness(url: string, options: ProbeOptions): Promise<Probe> {
  validateTarget(url);
  const timeoutMs = options.timeoutMs ?? 10_000;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 10_000) {
    throw new Error('invalid_probe_deadline');
  }
  const controller = new AbortController();
  const result = (reason: string, checkedAt: number | null = null): Probe =>
    ({ healthy: reason === 'ready', reason, checkedAt });
  let timeout = false;
  try {
    return await deadline((async () => {
      const response = await options.fetch(url, {
        method: 'GET', redirect: 'manual', signal: controller.signal,
        headers: { Accept: 'application/json', 'User-Agent': 'docagent-ops-readiness/1.0', 'Cache-Control': 'no-cache' },
      });
      if (response.status !== 200) {
        await response.body?.cancel();
        return result('http_status');
      }
      if (response.headers.get('content-type')?.split(';')[0]?.trim().toLowerCase() !== 'application/json') {
        await response.body?.cancel();
        return result('invalid_content_type');
      }
      const raw = await responseBytes(response);
      let data: unknown;
      try { data = JSON.parse(new TextDecoder('utf-8', { fatal: true, ignoreBOM: false }).decode(raw)); }
      catch { return result('invalid_readiness'); }
      if (!object(data) || !object(data.checks)) return result('invalid_readiness');
      const checks = data.checks;
      if (typeof data.checked_at !== 'string' ||
          !/^\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d:[0-5]\d(\.\d{1,6})?(Z|[+-]([01]\d|2[0-3]):[0-5]\d)$/.test(data.checked_at) ||
          !['ready', 'not_ready'].includes(String(data.status)) ||
          !['database', 'redis', 'object_store'].every((key) => key in checks) ||
          !Object.values(checks).every((check) => object(check) && ['up', 'down', 'timeout'].includes(String(check.status)))) {
        return result('invalid_readiness');
      }
      const checkedAt = Date.parse(data.checked_at);
      const age = options.now() - checkedAt;
      if (!Number.isFinite(age)) return result('invalid_readiness');
      const calendar = new Date(data.checked_at.slice(0, 10) + 'T00:00:00Z');
      if (!Number.isFinite(calendar.getTime()) || calendar.toISOString().slice(0, 10) !== data.checked_at.slice(0, 10)) {
        return result('invalid_readiness');
      }
      if (age < -15_000) return result('clock_skew', checkedAt);
      if (age > 120_000) return result('stale_readiness', checkedAt);
      if (data.status !== 'ready' || Object.values(checks).some((check) => !object(check) || check.status !== 'up')) {
        return result('dependency_unhealthy', checkedAt);
      }
      return result('ready', checkedAt);
    })(), timeoutMs, () => { timeout = true; controller.abort(); });
  } catch (error) {
    return result(timeout ? 'timeout' : error instanceof Error && error.message === 'response_too_large'
      ? 'response_too_large' : 'transport_error');
  }
}

type Mode = 'observe' | 'notify';
type Observation = { key: string; tick: number; observedAt: number; probe: Probe };
type Notification = {
  id: string; monitor_key: string; tick: number; kind: 'failure' | 'recovery';
  reason: string; delivery_status: 'pending' | 'suppressed' | 'attempting' | 'accepted' | 'unknown';
};

function validTick(tick: number, observedAt: number): boolean {
  return Number.isSafeInteger(tick) && tick >= 0 && tick % MINUTE === 0 &&
    Number.isSafeInteger(observedAt) && observedAt >= tick && observedAt - tick <= 90_000;
}

// D1 batch() is one transaction. Counter updates, event insertion and incident
// transitions commit together; a duplicate or out-of-order minute cannot count.
export async function recordObservation(db: D1Database, sample: Observation, mode: Mode): Promise<Notification | null> {
  if (!/^[a-z][a-z0-9-]{1,60}$/.test(sample.key) || !['observe', 'notify'].includes(mode)) {
    throw new Error('invalid_monitor_configuration');
  }
  if (!validTick(sample.tick, sample.observedAt)) return null;
  const { key, tick, observedAt, probe } = sample;
  const healthy = probe.healthy ? 1 : 0;
  const results = await db.batch([
    db.prepare('INSERT INTO monitor_state (monitor_key) VALUES (?) ON CONFLICT DO NOTHING').bind(key),
    db.prepare(`UPDATE monitor_state SET
      failures = CASE WHEN ? = 0 THEN CASE WHEN last_tick = ? - ${MINUTE} THEN min(failures + 1, 3) ELSE 1 END ELSE 0 END,
      successes = CASE WHEN ? = 1 THEN CASE WHEN last_tick = ? - ${MINUTE} THEN min(successes + 1, 2) ELSE 1 END ELSE 0 END,
      last_tick = ?, observed_at = ?, reason = ?
      WHERE monitor_key = ? AND last_tick < ?`).bind(healthy, tick, healthy, tick, tick, observedAt, probe.reason, key, tick),
    db.prepare(`INSERT INTO notification_events (id, monitor_key, tick, kind, reason, delivery_status)
      SELECT monitor_key || ':' || last_tick, monitor_key, last_tick,
        CASE WHEN incident_open = 0 THEN 'failure' ELSE 'recovery' END, reason, ?
      FROM monitor_state WHERE monitor_key = ? AND last_tick = ?
        AND ((incident_open = 0 AND failures = 3) OR (incident_open = 1 AND successes = 2))
      ON CONFLICT DO NOTHING RETURNING *`).bind(mode === 'notify' ? 'pending' : 'suppressed', key, tick),
    db.prepare(`UPDATE monitor_state SET incident_open = CASE
      WHEN failures = 3 THEN 1 WHEN successes = 2 THEN 0 ELSE incident_open END
      WHERE monitor_key = ? AND last_tick = ?`).bind(key, tick),
  ]);
  const events = results[2]?.results as Notification[] | undefined;
  return events?.[0] ?? null;
}

type DeliveryEnv = Pick<Env, 'MONITOR_DB' | 'MAIL' | 'MAIL_FROM' | 'MAIL_TO' | 'MODE' | 'MONITOR_KEY' | 'TARGET_URL'>;

// Only documented provider codes may leave the binding boundary. Error messages,
// arbitrary codes and recipient details never enter the diagnostic record.
const MAIL_ERROR_CODES = new Set([
  'E_VALIDATION_ERROR', 'E_FIELD_MISSING', 'E_TOO_MANY_RECIPIENTS', 'E_TOO_MANY_ATTACHMENTS',
  'E_SENDER_NOT_VERIFIED', 'E_RECIPIENT_NOT_ALLOWED', 'E_RECIPIENT_SUPPRESSED',
  'E_SENDER_DOMAIN_NOT_AVAILABLE', 'E_CONTENT_TOO_LARGE', 'E_DELIVERY_FAILED',
  'E_RATE_LIMIT_EXCEEDED', 'E_DAILY_LIMIT_EXCEEDED', 'E_INTERNAL_SERVER_ERROR',
  'E_HEADER_NOT_ALLOWED', 'E_HEADER_USE_API_FIELD', 'E_HEADER_VALUE_INVALID',
  'E_HEADER_VALUE_TOO_LONG', 'E_HEADER_NAME_INVALID', 'E_HEADERS_TOO_LARGE', 'E_HEADERS_TOO_MANY',
]);

export async function deliverNotification(
  env: DeliveryEnv, id: string, now: () => number, timeoutMs = 10_000,
): Promise<boolean> {
  if (env.MODE !== 'notify') return false;
  validateTarget(env.TARGET_URL);
  if (![env.MAIL_FROM, env.MAIL_TO].every((address) => /^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/.test(address)) ||
      !Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 10_000) {
    throw new Error('invalid_mail_configuration');
  }
  // Commit before touching the send binding. A crashed invocation leaves an
  // indeterminate attempt, never a pending event eligible for an automatic retry.
  const event = await env.MONITOR_DB.prepare(`UPDATE notification_events
    SET delivery_status = 'attempting', attempted_at = ?
    WHERE id = ? AND monitor_key = ? AND delivery_status = 'pending' RETURNING *`)
    .bind(now(), id, env.MONITOR_KEY).first<Notification>();
  if (!event) return false;
  const drill = event.monitor_key.startsWith('drill-');
  let accepted = false;
  let messageId: string | null = null;
  let diagnostic = 'invalid_receipt';
  let timedOut = false;
  try {
    const receipt = await deadline(env.MAIL.send({
      from: env.MAIL_FROM, to: env.MAIL_TO,
      subject: `[DocAgent]${drill ? '[TEST]' : ''} ${event.kind === 'failure' ? 'Service alert' : 'Service recovered'}`,
      text: [
        drill ? 'Controlled notification test. No production outage was injected.' : 'External readiness monitor.',
        `Event: ${event.id}`, `Time (UTC): ${new Date(event.tick).toISOString()}`,
        `Target: ${env.TARGET_URL}`, `State: ${event.kind}`, `Reason: ${event.reason}`,
        'Failure threshold: 3 consecutive minutes. Recovery threshold: 2 consecutive minutes.',
        'Check the service and the operations runbook. This receipt is not proof of inbox delivery.',
      ].join('\n'),
    }), timeoutMs, () => { timedOut = true; });
    if (receipt && typeof receipt.messageId === 'string' && /^[\x21-\x7e]{1,200}$/.test(receipt.messageId)) {
      accepted = true;
      messageId = receipt.messageId;
    }
  } catch (error) {
    // Rejection, timeout and unknown provider result all stay visible without
    // logging raw provider errors or retrying a possibly delivered message.
    diagnostic = timedOut ? 'timeout' : object(error) && typeof error.code === 'string' && MAIL_ERROR_CODES.has(error.code)
      ? error.code : 'provider_error';
  }
  const updates = [env.MONITOR_DB.prepare(`UPDATE notification_events
    SET delivery_status = ?, completed_at = ?, provider_message_id = ?
    WHERE id = ? AND delivery_status = 'attempting'`)
    .bind(accepted ? 'accepted' : 'unknown', now(), messageId, event.id)];
  if (!accepted) updates.push(env.MONITOR_DB.prepare(`INSERT INTO notification_diagnostics
    (event_id, code, recorded_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING`).bind(event.id, diagnostic, now()));
  await env.MONITOR_DB.batch(updates);
  return accepted;
}

type ScheduleResult = { status: 'ignored' | 'sampled'; healthy?: boolean; eventId?: string };

const BACKUP_PREFIX = 'operations-recovery/v1/';
const MAX_BACKUP_BYTES = 3 * 1024 ** 3;
const MAX_SNAPSHOT_BYTES = 64 * 1024 ** 2;

// This checks the trusted publisher's upload receipt and source freshness.
// Actual decryption/restore remains the separate trusted restore-heartbeat input.
export async function probeRemoteBackup(
  bucket: Pick<R2Bucket, 'list' | 'get' | 'head'>,
  options: { now: () => number; maxBytes: number; timeoutMs?: number },
): Promise<Probe> {
  const timeoutMs = options.timeoutMs ?? 10_000;
  if (!Number.isSafeInteger(options.maxBytes) || options.maxBytes < 1 || options.maxBytes > MAX_BACKUP_BYTES ||
      !Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 10_000) {
    throw new Error('invalid_backup_monitor_configuration');
  }
  const result = (reason: string, checkedAt: number | null = null): Probe =>
    ({ healthy: reason === 'backup_upload_fresh', reason, checkedAt });
  let expired = false;
  let cancelBody: (() => void) | undefined;
  const checkDeadline = () => { if (expired) throw new Error('deadline_exceeded'); };
  try {
    return await deadline((async () => {
      const objects = new Map<string, R2Object>();
      const cursors = new Set<string>();
      let cursor: string | undefined;
      let bytes = 0;
      let completeInventory = false;
      for (let page = 0; page < 5; page++) {
        checkDeadline();
        const listing = await bucket.list({ prefix: BACKUP_PREFIX, limit: 1000, cursor });
        checkDeadline();
        if (!Array.isArray(listing.objects) || listing.objects.length > 1000) return result('backup_remote_invalid');
        for (const item of listing.objects) {
          if (!/^operations-recovery\/v1\/snapshot-\d{8}-\d{6}-[a-f0-9]{12}\.(tar\.age|complete\.json)$/.test(item.key) ||
              objects.has(item.key) || !Number.isSafeInteger(item.size) || item.size < 1 ||
              typeof item.etag !== 'string' || !item.etag || item.etag.length > 256 ||
              !Number.isFinite(item.uploaded?.getTime()) || item.uploaded.getTime() < 0 ||
              item.uploaded.getTime() > options.now() + 15_000) return result('backup_remote_invalid');
          if (item.size > (item.key.endsWith('.tar.age') ? MAX_SNAPSHOT_BYTES : 4096)) {
            return result('backup_remote_invalid');
          }
          objects.set(item.key, item);
          bytes += item.size;
          if (objects.size > 4096) return result('backup_remote_inventory_limit');
        }
        if (!listing.truncated) { completeInventory = true; break; }
        cursor = listing.cursor;
        if (typeof cursor !== 'string' || !cursor || cursor.length > 8192 || cursors.has(cursor)) {
          return result('backup_remote_invalid');
        }
        cursors.add(cursor);
      }
      if (!completeInventory) return result('backup_remote_inventory_limit');
      if (!objects.size) return result('backup_remote_missing');
      // Reserve one maximum-size next snapshot, including its completion marker.
      if (bytes + MAX_SNAPSHOT_BYTES + 4096 > options.maxBytes) return result('backup_remote_capacity_low');
      const markers: R2Object[] = [];
      for (const item of objects.values()) {
        const marker = item.key.endsWith('.complete.json');
        const counterpart = marker ? item.key.replace(/\.complete\.json$/, '.tar.age')
          : item.key.replace(/\.tar\.age$/, '.complete.json');
        if (!objects.has(counterpart)) return result('backup_remote_upload_pending');
        if (marker) markers.push(item);
      }
      // The single server writer names captures by UTC start. Inspect the newest
      // capture's marker; a delayed upload must not refresh its source timestamp.
      let latest: { capturedAt: number; key: string; bytes: number } | undefined;
      markers.sort((a, b) => a.key < b.key ? 1 : a.key > b.key ? -1 : 0);
      for (const listed of markers.slice(0, 1)) {
        checkDeadline();
        const marker = await bucket.get(listed.key);
        checkDeadline();
        if (!marker || marker.size !== listed.size || marker.etag !== listed.etag) {
          await marker?.body.cancel();
          return result('backup_remote_changed');
        }
        const raw = await responseBytes(new Response(marker.body), 4096, cancel => { cancelBody = cancel; });
        checkDeadline();
        if (raw.byteLength !== listed.size) return result('backup_remote_invalid');
        let value: unknown;
        try { value = JSON.parse(new TextDecoder('utf-8', { fatal: true, ignoreBOM: false }).decode(raw)); }
        catch { return result('backup_remote_invalid'); }
        const sid = listed.key.slice(BACKUP_PREFIX.length, -'.complete.json'.length);
        const ciphertextKey = BACKUP_PREFIX + sid + '.tar.age';
        if (!object(value) || value.schema_version !== 1 || value.snapshot_id !== sid ||
            value.status !== 'ciphertext_upload_readback_verified' || value.ciphertext_key !== ciphertextKey ||
            typeof value.ciphertext_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(value.ciphertext_sha256) ||
            !Number.isSafeInteger(value.ciphertext_bytes) || Number(value.ciphertext_bytes) < 32 ||
            value.ciphertext_bytes !== objects.get(ciphertextKey)?.size ||
            typeof value.captured_at !== 'string' ||
            !/^\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d:[0-5]\d(\.\d{1,6})?(Z|\+00:00)$/.test(value.captured_at)) {
          return result('backup_remote_invalid');
        }
        const capturedAt = Date.parse(value.captured_at);
        const calendar = new Date(value.captured_at.slice(0, 10) + 'T00:00:00Z');
        if (!Number.isSafeInteger(capturedAt) || capturedAt < 0 || !Number.isFinite(calendar.getTime()) ||
            calendar.toISOString().slice(0, 10) !== value.captured_at.slice(0, 10) ||
            capturedAt > options.now() + 15_000 || capturedAt > listed.uploaded.getTime() + 15_000) {
          return result('backup_remote_invalid');
        }
        if (!latest || capturedAt > latest.capturedAt) {
          latest = { capturedAt, key: ciphertextKey, bytes: Number(value.ciphertext_bytes) };
        }
      }
      if (!latest) return result('backup_remote_missing');
      if (options.now() - latest.capturedAt > 300_000) return result('backup_remote_stale', latest.capturedAt);
      checkDeadline();
      const ciphertext = await bucket.head(latest.key);
      checkDeadline();
      if (!ciphertext || ciphertext.size !== latest.bytes || ciphertext.etag !== objects.get(latest.key)?.etag) {
        return result('backup_remote_changed');
      }
      return result('backup_upload_fresh', latest.capturedAt);
    })(), timeoutMs, () => { expired = true; cancelBody?.(); });
  } catch (error) {
    return result(expired ? 'backup_remote_timeout' : error instanceof Error && error.message === 'response_too_large'
      ? 'backup_remote_invalid' : 'backup_remote_unavailable');
  }
}

async function operationalProbe(env: Env, now: number, clock: () => number): Promise<Probe> {
  for (const [label, key, maxAge] of [
    ['queue', env.QUEUE_HEARTBEAT_KEY, 120_000],
    ['backup', env.BACKUP_HEARTBEAT_KEY, 300_000],
  ] as const) {
    if (!key) continue;
    const row = await env.MONITOR_DB.prepare('SELECT source_at, observed_at, healthy, artifact_sha256 FROM external_heartbeats WHERE source_key = ?')
      .bind(key).first<{ source_at: number; observed_at: number; healthy: number; artifact_sha256: string | null }>();
    const failed = (reason: string): Probe => ({ healthy: false, reason: label + '_' + reason, checkedAt: null });
    if (!row) return failed('missing');
    if (!Number.isSafeInteger(row.source_at) || !Number.isSafeInteger(row.observed_at) ||
        row.source_at < 0 || row.observed_at < 0 || ![0, 1].includes(row.healthy) ||
        row.source_at > now + 15_000 || row.observed_at > now + 15_000 || row.source_at > row.observed_at + 15_000) {
      return failed('invalid');
    }
    if (now - row.source_at > maxAge || now - row.observed_at > maxAge) return failed('stale');
    if (row.healthy !== 1) return failed('unhealthy');
    if (label === 'backup' && !/^[a-f0-9]{64}$/.test(row.artifact_sha256 ?? '')) return failed('unverified');
  }
  if (env.BACKUP_REMOTE_PREFIX) {
    const upload = await probeRemoteBackup(env.BACKUP_BUCKET, {
      now: clock, maxBytes: Number(env.BACKUP_REMOTE_MAX_BYTES),
    });
    if (!upload.healthy) return upload;
  }
  if (env.WATCH_MONITOR_KEY) {
    const row = await env.MONITOR_DB.prepare('SELECT observed_at FROM monitor_state WHERE monitor_key = ?')
      .bind(env.WATCH_MONITOR_KEY).first<{ observed_at: number | null }>();
    if (row?.observed_at == null) return { healthy: false, reason: 'monitor_missing', checkedAt: null };
    if (!Number.isSafeInteger(row.observed_at) || row.observed_at < 0 || row.observed_at > now + 15_000) {
      return { healthy: false, reason: 'monitor_invalid', checkedAt: null };
    }
    if (now - row.observed_at > 180_000) return { healthy: false, reason: 'monitor_stale', checkedAt: null };
  }
  return { healthy: true, reason: 'ready', checkedAt: now };
}

export async function runScheduled(env: Env, scheduledTime: number, boundary: ProbeOptions): Promise<ScheduleResult> {
  const tick = Math.floor(scheduledTime / MINUTE) * MINUTE;
  const drillStart = Number(env.DRILL_START_MS);
  if (!['observe', 'notify'].includes(env.MODE) || !/^[a-z][a-z0-9-]{1,60}$/.test(env.MONITOR_KEY) ||
      !/^\d+$/.test(env.DRILL_START_MS) || !Number.isSafeInteger(drillStart) ||
      drillStart % MINUTE !== 0 || (drillStart > 0) !== env.MONITOR_KEY.startsWith('drill-')) {
    throw new Error('invalid_monitor_configuration');
  }
  validateTarget(env.TARGET_URL);
  if (![undefined, 'true', 'false'].includes(env.CHECK_READINESS) ||
      [env.QUEUE_HEARTBEAT_KEY, env.BACKUP_HEARTBEAT_KEY, env.WATCH_MONITOR_KEY]
        .some(key => key && !/^[a-z][a-z0-9-]{1,60}$/.test(key)) ||
      (env.WATCH_MONITOR_KEY && env.WATCH_MONITOR_KEY === env.MONITOR_KEY) ||
      (env.CHECK_READINESS === 'false' && !env.WATCH_MONITOR_KEY) ||
      (env.BACKUP_REMOTE_PREFIX && (env.BACKUP_REMOTE_PREFIX !== BACKUP_PREFIX || !env.BACKUP_BUCKET ||
        !/^\d+$/.test(env.BACKUP_REMOTE_MAX_BYTES) || Number(env.BACKUP_REMOTE_MAX_BYTES) < 1 ||
        Number(env.BACKUP_REMOTE_MAX_BYTES) > MAX_BACKUP_BYTES))) {
    throw new Error('invalid_operational_monitor_configuration');
  }
  if (!validTick(tick, boundary.now())) return { status: 'ignored' };
  if (env.MODE === 'notify') {
    const pending = await env.MONITOR_DB.prepare(`SELECT id FROM notification_events
      WHERE monitor_key = ? AND delivery_status = 'pending' ORDER BY tick LIMIT 2`)
      .bind(env.MONITOR_KEY).all<{ id: string }>();
    for (const event of pending.results) await deliverNotification(env, event.id, boundary.now);
  }
  const previous = await env.MONITOR_DB.prepare('SELECT last_tick FROM monitor_state WHERE monitor_key = ?')
    .bind(env.MONITOR_KEY).first<{ last_tick: number }>();
  if (previous && previous.last_tick >= tick) return { status: 'ignored' };
  let probe: Probe;
  if (drillStart > 0) {
    const minute = (tick - drillStart) / MINUTE;
    if (minute < 0 || minute >= 5) return { status: 'ignored' };
    probe = { healthy: minute >= 3, reason: minute >= 3 ? 'drill_recovery' : 'drill_failure', checkedAt: null };
  } else {
    probe = env.CHECK_READINESS === 'false'
      ? { healthy: true, reason: 'ready', checkedAt: null }
      : await probeReadiness(env.TARGET_URL, boundary);
    if (probe.healthy) probe = await operationalProbe(env, boundary.now(), boundary.now);
  }
  const observedAt = boundary.now();
  if (!validTick(tick, observedAt)) return { status: 'ignored' };
  const event = await recordObservation(env.MONITOR_DB, { key: env.MONITOR_KEY, tick, observedAt, probe }, env.MODE as Mode);
  if (event) await deliverNotification(env, event.id, boundary.now);
  return { status: 'sampled', healthy: probe.healthy, ...(event ? { eventId: event.id } : {}) };
}

export default {
  fetch() { return new Response('Not found', { status: 404 }); },
  async scheduled(controller, env) {
    try {
      const result = await runScheduled(env, controller.scheduledTime, {
        fetch: (url, options) => fetch(url, options), now: Date.now,
      });
      console.log(JSON.stringify({ event: 'readiness_monitor', ...result }));
    } catch {
      // Keep raw network/D1/email exceptions and account information out of logs.
      console.error(JSON.stringify({ event: 'readiness_monitor_failed' }));
      throw new Error('readiness_monitor_failed');
    }
  },
} satisfies ExportedHandler<Env>;
