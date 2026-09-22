import { z } from "zod";

export const browserTenantSchema = z.object({
  tenantId: z.string().uuid(), name: z.string().min(1).max(200),
  actorId: z.string().uuid(), role: z.enum(["owner", "member"]),
}).strict();

export const browserAuthenticatedSchema = z.object({
  status: z.literal("authenticated"), email: z.string().email().max(320),
  expiresAt: z.iso.datetime({ offset: true }),
  contextVersion: z.string().regex(/^[0-9a-f]{32}\.[1-9][0-9]{0,18}$/),
  csrfToken: z.string().regex(/^[0-9a-f]{64}$/),
  currentTenant: browserTenantSchema.nullable(),
  loginProvider: z.literal("github").optional(),
}).strict();

export const browserSessionSchema = z.union([
  browserAuthenticatedSchema,
  z.object({ status: z.literal("anonymous"), loginProvider: z.literal("github").optional() }).strict(),
  z.object({ status: z.literal("disabled") }).strict(),
]);
export const browserTenantsSchema = z.array(browserTenantSchema).max(1000);
export const admissionInputSchema = z.object({
  token: z.string().regex(/^adm1_[A-Za-z0-9_-]{43}$/),
  tenantName: z.string().trim().min(1).max(200),
}).strict();
export const admissionReceiptSchema = z.object({ tenantId: z.string().uuid(), replayed: z.boolean() }).strict();
export const browserLogoutSchema = z.object({ revoked: z.literal(true) }).strict();

export type BrowserAuthenticatedSession = z.infer<typeof browserAuthenticatedSchema>;
export type BrowserTenant = z.infer<typeof browserTenantSchema>;
