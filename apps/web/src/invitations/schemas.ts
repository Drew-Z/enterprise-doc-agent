import { z } from "zod";

export const invitationTokenSchema = z.string().length(48).regex(/^inv1_[A-Za-z0-9_-]{43}$/);
export const invitationInputSchema = z.object({ token: invitationTokenSchema }).strict();
export const invitationStateSchema = z.enum(["pending", "expired", "accepted", "revoked"]);
export const invitationSchema = z.object({
  invitationId: z.string().uuid(),
  email: z.string().email().max(320),
  state: invitationStateSchema,
  generation: z.number().int().positive(),
  expiresAt: z.iso.datetime({ offset: true }),
  createdAt: z.iso.datetime({ offset: true }),
  updatedAt: z.iso.datetime({ offset: true }),
}).strict();
export const invitationListSchema = z.object({
  items: z.array(invitationSchema).max(100),
  hasMore: z.boolean(),
  eligible: z.boolean(),
  seats: z.object({
    active: z.number().int().nonnegative(),
    limit: z.number().int().positive().nullable(),
    remaining: z.number().int().nonnegative().nullable(),
  }).strict(),
}).strict();
export const invitationMutationSchema = z.object({
  invitation: invitationSchema,
  replayed: z.boolean(),
  token: invitationTokenSchema.nullable(),
}).strict();
export const invitationPreviewSchema = z.object({
  tenantName: z.string().min(1).max(200),
  expiresAt: z.iso.datetime({ offset: true }),
  state: invitationStateSchema,
}).strict();
export const invitationReceiptSchema = z.object({
  tenantId: z.string().uuid(),
  tenantName: z.string().min(1).max(200),
  membershipId: z.string().uuid(),
  replayed: z.boolean(),
}).strict();
export type Invitation = z.infer<typeof invitationSchema>;
export type InvitationList = z.infer<typeof invitationListSchema>;
export type InvitationMutation = z.infer<typeof invitationMutationSchema>;
export type InvitationPreview = z.infer<typeof invitationPreviewSchema>;
