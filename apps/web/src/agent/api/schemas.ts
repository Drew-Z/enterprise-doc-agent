import { z } from "zod";

const uuidSchema = z.string().uuid();
const dateTimeSchema = z.iso.datetime({ offset: true });
const safeIntegerSchema = z.number().int().safe();
const positiveSafeIntegerSchema = safeIntegerSchema.positive();
const httpUrlSchema = z.url().refine((value) => {
  const protocol = new URL(value).protocol;
  return protocol === "http:" || protocol === "https:";
}, "Expected an HTTP(S) URL");

export const agentRunTaskTypeSchema = z.enum([
  "question_answer",
  "summary",
  "structured_extraction",
]);

export const agentRunStatusSchema = z.enum([
  "pending",
  "running",
  "waiting_approval",
  "succeeded",
  "refused",
  "failed",
  "cancelled",
  "rejected",
  "expired",
]);

export const agentEventTypeSchema = z.enum([
  "run.created",
  "run.cancel_requested",
  "run.cancelled",
  "run.started",
  "run.waiting_approval",
  "run.resumed",
  "run.finished",
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

export const readyDocumentVersionSchema = z
  .object({
    versionId: uuidSchema,
    documentId: uuidSchema,
    generationId: uuidSchema,
    filename: z.string().min(1),
    sizeBytes: positiveSafeIntegerSchema,
    contentSha256: z.string().regex(/^[0-9a-f]{64}$/),
    createdAt: dateTimeSchema,
  })
  .strict();

export const createAgentRunRequestSchema = z
  .object({
    documentVersionId: uuidSchema,
    taskType: agentRunTaskTypeSchema,
    inputText: z.string().min(1).max(20_000),
    extractionSchema: z.record(z.string(), z.unknown()).nullable().optional(),
    publishRequested: z.boolean(),
  })
  .strict();

export const createAgentRunResponseSchema = z
  .object({
    runId: uuidSchema,
    jobId: uuidSchema,
    status: z.string().min(1),
    replayed: z.boolean(),
    createdAt: dateTimeSchema,
  })
  .strict();

export const agentRunAttemptSchema = z
  .object({
    attemptId: uuidSchema,
    attemptNumber: positiveSafeIntegerSchema,
    status: z.string().min(1),
    workerId: z.string().min(1),
    startedAt: dateTimeSchema,
    heartbeatAt: dateTimeSchema.nullable(),
    finishedAt: dateTimeSchema.nullable(),
    errorCode: z.string().nullable(),
  })
  .strict();

export const agentRunExecutionSchema = z
  .object({
    executionId: uuidSchema,
    sequence: safeIntegerSchema.nonnegative(),
    kind: z.string().min(1),
    jobId: uuidSchema,
    jobStatus: z.string().min(1),
    attempts: safeIntegerSchema.nonnegative(),
    maxAttempts: positiveSafeIntegerSchema,
    cancelRequested: z.boolean(),
    attemptHistory: z.array(agentRunAttemptSchema),
  })
  .strict();

export const agentRunStatusResponseSchema = z
  .object({
    runId: uuidSchema,
    tenantId: uuidSchema,
    documentVersionId: uuidSchema,
    taskType: agentRunTaskTypeSchema,
    publishRequested: z.boolean(),
    status: agentRunStatusSchema,
    graphVersion: z.string().min(1),
    promptVersion: z.string().min(1),
    modelProvider: z.string().min(1),
    modelName: z.string().min(1),
    modelVersion: z.string().nullable(),
    toolSchemaVersion: z.string().min(1),
    currentExecutionSeq: safeIntegerSchema.nonnegative(),
    errorCode: z.string().nullable(),
    createdAt: dateTimeSchema,
    startedAt: dateTimeSchema.nullable(),
    waitingAt: dateTimeSchema.nullable(),
    finishedAt: dateTimeSchema.nullable(),
    cancelledAt: dateTimeSchema.nullable(),
    executions: z.array(agentRunExecutionSchema),
  })
  .strict();

export const agentRunEventResponseSchema = z
  .object({
    eventId: uuidSchema,
    seq: positiveSafeIntegerSchema,
    eventType: agentEventTypeSchema,
    eventVersion: positiveSafeIntegerSchema,
    publicPayload: z.record(z.string(), z.unknown()),
    createdAt: dateTimeSchema,
  })
  .strict();

export const runCreatedPayloadSchema = z
  .object({
    task_type: agentRunTaskTypeSchema,
    document_version_id: uuidSchema,
    publish_requested: z.boolean(),
  })
  .strict();

export const runStatusPayloadSchema = z
  .object({ status: z.literal("running") })
  .strict();

export const runCancelledPayloadSchema = z
  .object({ status: z.literal("cancelled") })
  .strict();

export const runWaitingApprovalPayloadSchema = z
  .object({ status: z.literal("waiting_approval"), approval_id: uuidSchema })
  .strict();

export const runFinishedPayloadSchema = z
  .object({
    status: z.enum(["succeeded", "refused", "rejected", "expired"]),
    refusal_reason: z.string().nullable().optional(),
  })
  .strict();

export const agentSseDataSchema = z
  .object({
    createdAt: dateTimeSchema,
    eventType: agentEventTypeSchema,
    eventVersion: positiveSafeIntegerSchema,
    payload: z.record(z.string(), z.unknown()),
  })
  .strict();

export const approvalRequestResponseSchema = z
  .object({
    approvalId: uuidSchema,
    runId: uuidSchema,
    status: z.enum(["pending", "approved", "rejected", "expired", "revoked", "consumed"]),
    operation: z.literal("publish_artifact"),
    targetResourceType: z.literal("agent_artifact"),
    targetResourceId: uuidSchema,
    targetDocumentVersionId: uuidSchema,
    targetFingerprint: z.string().regex(/^[0-9a-f]{64}$/),
    requestedAt: dateTimeSchema,
    expiresAt: dateTimeSchema,
    decidedAt: dateTimeSchema.nullable(),
    canDecide: z.boolean(),
  })
  .strict();

export const approvalDecisionRequestSchema = z
  .object({
    decision: z.enum(["approved", "rejected"]),
    operation: z.literal("publish_artifact"),
    targetResourceType: z.literal("agent_artifact"),
    targetResourceId: uuidSchema,
    targetDocumentVersionId: uuidSchema,
    targetFingerprint: z.string().regex(/^[0-9a-f]{64}$/),
    comment: z.string().max(1000).nullable().optional(),
  })
  .strict();

export const approvalDecisionResponseSchema = z
  .object({
    approvalId: uuidSchema,
    runId: uuidSchema,
    status: z.string().min(1),
    decision: z.string().min(1),
    resumeJobId: uuidSchema,
    resumeExecutionId: uuidSchema,
    decisionFingerprint: z.string().regex(/^[0-9a-f]{64}$/),
    replayed: z.boolean(),
    decidedAt: dateTimeSchema,
  })
  .strict();

export const agentArtifactResponseSchema = z
  .object({
    artifactId: uuidSchema,
    runId: uuidSchema,
    documentVersionId: uuidSchema,
    kind: z.string().min(1),
    status: z.enum(["writing", "draft_ready", "published", "failed", "revoked"]),
    contentType: z.string().min(1),
    contentSha256: z.string().regex(/^[0-9a-f]{64}$/),
    sizeBytes: safeIntegerSchema.nonnegative(),
    createdAt: dateTimeSchema,
    verifiedAt: dateTimeSchema,
    publishedAt: dateTimeSchema.nullable(),
  })
  .strict();

export const agentArtifactDownloadResponseSchema = z
  .object({
    artifactId: uuidSchema,
    status: z.enum(["draft_ready", "published"]),
    contentType: z.string().min(1),
    contentSha256: z.string().regex(/^[0-9a-f]{64}$/),
    sizeBytes: safeIntegerSchema.nonnegative(),
    url: httpUrlSchema,
    expiresInSeconds: positiveSafeIntegerSchema,
  })
  .strict();

export const persistedAgentRunSchema = z
  .object({
    version: z.literal(1),
    runId: uuidSchema,
    lastSequence: safeIntegerSchema.nonnegative(),
  })
  .strict();

export type AgentRunTaskType = z.infer<typeof agentRunTaskTypeSchema>;
export type AgentRunStatus = z.infer<typeof agentRunStatusSchema>;
export type AgentEventType = z.infer<typeof agentEventTypeSchema>;
export type ReadyDocumentVersion = z.infer<typeof readyDocumentVersionSchema>;
export type CreateAgentRunRequest = z.infer<typeof createAgentRunRequestSchema>;
export type CreateAgentRunResponse = z.infer<typeof createAgentRunResponseSchema>;
export type AgentRunStatusResponse = z.infer<typeof agentRunStatusResponseSchema>;
export type AgentRunEventResponse = z.infer<typeof agentRunEventResponseSchema>;
export type AgentSseData = z.infer<typeof agentSseDataSchema>;
export type ApprovalRequestResponse = z.infer<typeof approvalRequestResponseSchema>;
export type ApprovalDecisionRequest = z.infer<typeof approvalDecisionRequestSchema>;
export type ApprovalDecisionResponse = z.infer<typeof approvalDecisionResponseSchema>;
export type AgentArtifactResponse = z.infer<typeof agentArtifactResponseSchema>;
export type AgentArtifactDownloadResponse = z.infer<typeof agentArtifactDownloadResponseSchema>;
export type PersistedAgentRun = z.infer<typeof persistedAgentRunSchema>;

export function validateSsePayload(eventType: AgentEventType, payload: unknown): Record<string, unknown> {
  const schemas: Partial<Record<AgentEventType, z.ZodType<Record<string, unknown>>>> = {
    "run.created": runCreatedPayloadSchema,
    "run.cancel_requested": runStatusPayloadSchema,
    "run.cancelled": runCancelledPayloadSchema,
    "run.started": runStatusPayloadSchema,
    "run.waiting_approval": runWaitingApprovalPayloadSchema,
    "run.resumed": runStatusPayloadSchema,
    "run.finished": runFinishedPayloadSchema,
  };
  const schema = schemas[eventType];
  if (schema === undefined) {
    throw new Error("Unsupported Agent event type.");
  }
  return schema.parse(payload);
}
