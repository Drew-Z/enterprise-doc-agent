import { z, type ZodType } from "zod";

import { errorResponseSchema } from "../agent/api/schemas";

const uuidSchema = z.string().uuid();
const dateTimeSchema = z.iso.datetime({ offset: true });

export const auditRetentionPolicySchema = z.object({
  tenantId: uuidSchema,
  retentionDays: z.number().int().min(30).max(3650),
  isEnabled: z.boolean(),
  updatedBy: uuidSchema.nullable(),
}).strict();

export const auditRetentionPreviewSchema = z.object({
  cutoffAt: dateTimeSchema.nullable(),
  eligibleEventCount: z.number().int().nonnegative(),
  protectedEventCount: z.number().int().nonnegative(),
}).strict();

export const auditRetentionPlanSchema = z.object({
  policy: auditRetentionPolicySchema,
  cutoffAt: dateTimeSchema.nullable(),
  eligibleEventCount: z.number().int().nonnegative(),
  protectedEventCount: z.number().int().nonnegative(),
  eligibleEventIds: z.array(uuidSchema),
  fingerprint: z.string().regex(/^[0-9a-f]{64}$/),
}).strict();

export const auditArchiveBatchSchema = z.object({
  batchId: uuidSchema,
  tenantId: uuidSchema,
  cutoffAt: dateTimeSchema,
  archivedEventCount: z.number().int().nonnegative(),
  fingerprint: z.string().regex(/^[0-9a-f]{64}$/),
  bucket: z.string().min(1),
  objectKey: z.string().min(1),
  contentSha256: z.string().regex(/^[0-9a-f]{64}$/),
  sizeBytes: z.number().int().positive(),
  createdBy: uuidSchema.nullable(),
  createdAt: dateTimeSchema.nullable().optional(),
}).strict();

export const auditArchiveVerificationSchema = z.object({
  batchId: uuidSchema,
  tenantId: uuidSchema,
  verifiedAt: dateTimeSchema,
  valid: z.boolean(),
  expectedSha256: z.string().regex(/^[0-9a-f]{64}$/),
  actualSha256: z.string().regex(/^[0-9a-f]{64}$/).nullable(),
  expectedSizeBytes: z.number().int().nonnegative(),
  actualSizeBytes: z.number().int().nonnegative().nullable(),
  envelopeValid: z.boolean(),
  failureReason: z.string().nullable(),
}).strict();

export const auditArchiveDownloadSchema = z.object({
  batchId: uuidSchema,
  tenantId: uuidSchema,
  bucket: z.string().min(1),
  objectKey: z.string().min(1),
  contentSha256: z.string().regex(/^[0-9a-f]{64}$/),
  sizeBytes: z.number().int().positive(),
  url: z.string().url(),
  expiresInSeconds: z.number().int().min(60).max(900),
}).strict();

export const auditLegalHoldSchema = z.object({
  holdId: uuidSchema,
  tenantId: uuidSchema,
  name: z.string().min(1),
  reason: z.string().min(1),
  resourceType: z.string().nullable(),
  resourceId: uuidSchema.nullable(),
  startsAt: dateTimeSchema,
  expiresAt: dateTimeSchema.nullable(),
  releasedAt: dateTimeSchema.nullable(),
  createdBy: uuidSchema.nullable(),
  releasedBy: uuidSchema.nullable(),
}).strict();

export const auditLegalHoldCreateRequestSchema = z.object({
  name: z.string().trim().min(1).max(200),
  reason: z.string().trim().min(1).max(2000),
  resourceType: z.string().trim().min(1).max(80).optional(),
  resourceId: uuidSchema.optional(),
  startsAt: dateTimeSchema.optional(),
  expiresAt: dateTimeSchema.optional(),
}).strict();

export type AuditRetentionPolicy = z.infer<typeof auditRetentionPolicySchema>;
export type AuditRetentionPreview = z.infer<typeof auditRetentionPreviewSchema>;
export type AuditRetentionPlan = z.infer<typeof auditRetentionPlanSchema>;
export type AuditArchiveBatch = z.infer<typeof auditArchiveBatchSchema>;
export type AuditArchiveVerification = z.infer<typeof auditArchiveVerificationSchema>;
export type AuditArchiveDownload = z.infer<typeof auditArchiveDownloadSchema>;
export type AuditLegalHold = z.infer<typeof auditLegalHoldSchema>;
export type AuditLegalHoldCreateRequest = z.infer<typeof auditLegalHoldCreateRequestSchema>;

function normalizeBaseUrl(value: string | undefined): string {
  return (value ?? "").replace(/\/+$/, "");
}

export class AuditGovernanceApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId: string | null = null,
  ) {
    super(message);
    this.name = "AuditGovernanceApiError";
  }
}

async function requestJson<T>(
  token: string,
  path: string,
  schema: ZodType<T>,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL)}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    const parsed = errorResponseSchema.safeParse(await response.json().catch(() => null));
    throw new AuditGovernanceApiError(
      response.status,
      parsed.success ? parsed.data.error.code : "audit_governance_request_failed",
      parsed.success ? parsed.data.error.message : `Audit governance request failed (${response.status}).`,
      parsed.success ? parsed.data.error.requestId : null,
    );
  }
  const parsed = schema.safeParse(await response.json());
  if (!parsed.success) throw new Error("Audit governance response schema is invalid.");
  return parsed.data;
}

export function fetchAuditRetentionPolicy(token: string, signal?: AbortSignal): Promise<AuditRetentionPolicy> {
  return requestJson(token, "/api/audit-governance/retention-policy", auditRetentionPolicySchema, { signal });
}

export function updateAuditRetentionPolicy(
  token: string,
  request: Pick<AuditRetentionPolicy, "retentionDays" | "isEnabled">,
  signal?: AbortSignal,
): Promise<AuditRetentionPolicy> {
  const payload = auditRetentionPolicySchema.pick({ retentionDays: true, isEnabled: true }).parse(request);
  return requestJson(token, "/api/audit-governance/retention-policy", auditRetentionPolicySchema, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
}

export function fetchAuditRetentionPreview(token: string, signal?: AbortSignal): Promise<AuditRetentionPreview> {
  return requestJson(token, "/api/audit-governance/retention-preview", auditRetentionPreviewSchema, { signal });
}

export function fetchAuditRetentionPlan(
  token: string,
  limit = 100,
  signal?: AbortSignal,
): Promise<AuditRetentionPlan> {
  const boundedLimit = z.number().int().min(1).max(500).parse(limit);
  return requestJson(
    token,
    `/api/audit-governance/retention-plan?limit=${boundedLimit}`,
    auditRetentionPlanSchema,
    { signal },
  );
}

export function archiveAuditRetentionPlan(
  token: string,
  limit = 100,
  signal?: AbortSignal,
): Promise<AuditArchiveBatch> {
  const boundedLimit = z.number().int().min(1).max(500).parse(limit);
  return requestJson(
    token,
    `/api/audit-governance/retention-archive?limit=${boundedLimit}`,
    auditArchiveBatchSchema,
    { method: "POST", signal },
  );
}

export function fetchAuditArchiveBatches(
  token: string,
  limit = 25,
  signal?: AbortSignal,
): Promise<AuditArchiveBatch[]> {
  const boundedLimit = z.number().int().min(1).max(100).parse(limit);
  return requestJson(
    token,
    `/api/audit-governance/retention-archives?limit=${boundedLimit}`,
    z.array(auditArchiveBatchSchema),
    { signal },
  );
}

export function verifyAuditArchiveBatch(
  token: string,
  batchId: string,
  signal?: AbortSignal,
): Promise<AuditArchiveVerification> {
  const path = `/api/audit-governance/retention-archives/${encodeURIComponent(uuidSchema.parse(batchId))}/verify`;
  return requestJson(token, path, auditArchiveVerificationSchema, { method: "POST", signal });
}

export function fetchAuditArchiveDownload(
  token: string,
  batchId: string,
  expiresInSeconds = 300,
  signal?: AbortSignal,
): Promise<AuditArchiveDownload> {
  const boundedExpiry = z.number().int().min(60).max(900).parse(expiresInSeconds);
  const path = `/api/audit-governance/retention-archives/${encodeURIComponent(uuidSchema.parse(batchId))}/download?expiresIn=${boundedExpiry}`;
  return requestJson(token, path, auditArchiveDownloadSchema, { signal });
}

export function fetchAuditLegalHolds(token: string, signal?: AbortSignal): Promise<AuditLegalHold[]> {
  return requestJson(token, "/api/audit-governance/legal-holds", z.array(auditLegalHoldSchema), { signal });
}

export function createAuditLegalHold(
  token: string,
  request: AuditLegalHoldCreateRequest,
  signal?: AbortSignal,
): Promise<AuditLegalHold> {
  const payload = auditLegalHoldCreateRequestSchema.parse(request);
  return requestJson(token, "/api/audit-governance/legal-holds", auditLegalHoldSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
}

export function releaseAuditLegalHold(token: string, holdId: string, signal?: AbortSignal): Promise<AuditLegalHold> {
  const path = `/api/audit-governance/legal-holds/${encodeURIComponent(uuidSchema.parse(holdId))}`;
  return requestJson(token, path, auditLegalHoldSchema, { method: "DELETE", signal });
}
