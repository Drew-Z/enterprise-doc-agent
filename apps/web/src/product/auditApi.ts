import { z } from "zod";

const auditEventSchema = z.object({
  eventId: z.string().uuid(),
  tenantId: z.string().uuid(),
  actorId: z.string().uuid().nullable(),
  action: z.string(),
  resourceType: z.string(),
  resourceId: z.string().uuid().nullable(),
  occurredAt: z.string(),
  requestId: z.string().nullable(),
  correlationId: z.string().nullable(),
  metadata: z.record(z.string(), z.unknown()),
  schemaVersion: z.number().int().positive(),
});

const auditPageSchema = z.object({
  items: z.array(auditEventSchema),
  nextCursor: z.string().nullable(),
});

export type AuditEvent = z.infer<typeof auditEventSchema>;
export type AuditPage = z.infer<typeof auditPageSchema>;

export interface AuditQuery {
  action?: string;
  resourceType?: string;
  from?: string;
  to?: string;
  cursor?: string;
  limit?: number;
}

function normalizeBaseUrl(value: string | undefined): string {
  return (value ?? "").replace(/\/+$/, "");
}

function buildAuditParams(query: AuditQuery): URLSearchParams {
  const params = new URLSearchParams({ limit: String(query.limit ?? 100) });
  if (query.action) params.set("action", query.action);
  if (query.resourceType) params.set("resourceType", query.resourceType);
  if (query.from) params.set("from", query.from);
  if (query.to) params.set("to", query.to);
  if (query.cursor) params.set("cursor", query.cursor);
  return params;
}

export async function fetchAuditEvents(
  token: string,
  query: AuditQuery = {},
  signal?: AbortSignal,
): Promise<AuditPage> {
  const params = buildAuditParams(query);
  const response = await fetch(`${normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL)}/api/audit-events?${params}`, {
    headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
    signal,
  });
  if (!response.ok) throw new Error(`Audit log request failed (${response.status}).`);
  const parsed = auditPageSchema.safeParse(await response.json());
  if (!parsed.success) throw new Error("Audit log response schema is invalid.");
  return parsed.data;
}

export async function exportAuditEvents(
  token: string,
  query: AuditQuery = {},
  signal?: AbortSignal,
): Promise<Blob> {
  const params = buildAuditParams({ ...query, limit: query.limit ?? 2000 });
  const response = await fetch(`${normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL)}/api/audit-events/export.csv?${params}`, {
    headers: { Accept: "text/csv", Authorization: `Bearer ${token}` },
    signal,
  });
  if (!response.ok) throw new Error(`Audit export request failed (${response.status}).`);
  return response.blob();
}
