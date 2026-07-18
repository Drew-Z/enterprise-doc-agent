import { z } from "zod";

const safeIntegerSchema = z.number().int().safe();
const positiveSafeIntegerSchema = safeIntegerSchema.positive();
export const sessionIdSchema = z.string().uuid();
export const partNumberSchema = positiveSafeIntegerSchema.max(10_000);
const dateTimeSchema = z.iso.datetime({ offset: true });
const httpUrlSchema = z.url().refine((value) => {
  const protocol = new URL(value).protocol;
  return protocol === "http:" || protocol === "https:";
}, "Expected an HTTP(S) URL");

export const sha256HexSchema = z.string().regex(/^[0-9a-f]{64}$/);
export const sha256Base64Schema = z.string().refine((value) => {
  if (!/^[A-Za-z0-9+/]{43}=$/.test(value)) {
    return false;
  }
  try {
    const decoded = atob(value);
    return decoded.length === 32 && btoa(decoded) === value;
  } catch {
    return false;
  }
}, "Expected a canonical base64 SHA-256 digest");

export const uploadSessionStatusSchema = z.enum([
  "initializing",
  "active",
  "completing",
  "completed",
  "aborted",
  "expired",
  "failed",
]);

export const errorResponseSchema = z
  .object({
    error: z
      .object({
        code: z.string().min(1),
        message: z.string().min(1),
        requestId: z.string().nullable(),
      })
      .strict(),
  })
  .strict();

export const createUploadRequestSchema = z
  .object({
    filename: z.string().min(1),
    sizeBytes: positiveSafeIntegerSchema,
    mediaType: z.string().min(1),
    sha256: sha256HexSchema,
  })
  .strict();

export const createUploadResponseSchema = z
  .object({
    sessionId: sessionIdSchema,
    status: uploadSessionStatusSchema,
    filename: z.string().min(1),
    extension: z.string().min(1),
    mediaType: z.string().min(1),
    sizeBytes: positiveSafeIntegerSchema,
    declaredSha256: sha256HexSchema,
    partSizeBytes: positiveSafeIntegerSchema,
    expectedPartCount: positiveSafeIntegerSchema.max(10_000),
    expiresAt: dateTimeSchema,
    replayed: z.boolean(),
  })
  .strict();

export const uploadedPartSchema = z
  .object({
    partNumber: partNumberSchema,
    sizeBytes: positiveSafeIntegerSchema,
    etag: z.string().min(1),
    checksumSha256: sha256Base64Schema,
  })
  .strict();

export const getUploadResponseSchema = createUploadResponseSchema
  .omit({ replayed: true })
  .extend({ uploadedParts: z.array(uploadedPartSchema).max(10_000) })
  .strict();

export const presignPartRequestSchema = z
  .object({
    sizeBytes: positiveSafeIntegerSchema,
    checksumSha256: sha256Base64Schema,
  })
  .strict();

export const presignPartResponseSchema = z
  .object({
    partNumber: partNumberSchema,
    sizeBytes: positiveSafeIntegerSchema,
    checksumSha256: sha256Base64Schema,
    url: httpUrlSchema,
    headers: z.record(z.string(), z.string()),
    expiresInSeconds: positiveSafeIntegerSchema,
  })
  .strict();

export const completeUploadRequestSchema = z
  .object({
    parts: z.array(uploadedPartSchema).min(1).max(10_000),
  })
  .strict();

export const completeUploadResponseSchema = z
  .object({
    sessionId: sessionIdSchema,
    status: z.literal("completed"),
    documentId: z.string().uuid(),
    versionId: z.string().uuid(),
    completedAt: dateTimeSchema,
    replayed: z.boolean(),
  })
  .strict();

export type UploadSessionStatus = z.infer<typeof uploadSessionStatusSchema>;
export type ErrorResponse = z.infer<typeof errorResponseSchema>;
export type CreateUploadRequest = z.infer<typeof createUploadRequestSchema>;
export type CreateUploadResponse = z.infer<typeof createUploadResponseSchema>;
export type UploadedPart = z.infer<typeof uploadedPartSchema>;
export type GetUploadResponse = z.infer<typeof getUploadResponseSchema>;
export type PresignPartRequest = z.infer<typeof presignPartRequestSchema>;
export type PresignPartResponse = z.infer<typeof presignPartResponseSchema>;
export type CompleteUploadRequest = z.infer<typeof completeUploadRequestSchema>;
export type CompleteUploadResponse = z.infer<typeof completeUploadResponseSchema>;
