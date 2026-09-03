import type { ReadinessResponse } from "../api/health";
import type {
  AgentArtifactPreviewResponse,
  AgentArtifactResponse,
  AgentRunEventResponse,
  AgentRunStatusResponse,
  ReadyDocumentVersion,
} from "../agent/api/schemas";

const tenantId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const documentId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const documentVersionId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const generationId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const runId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
const jobId = "ffffffff-ffff-4fff-8fff-ffffffffffff";
const executionId = "11111111-2222-4333-8444-555555555555";
const attemptId = "66666666-7777-4888-8999-000000000000";
const artifactId = "12345678-1234-4234-8234-123456789012";
const chunkId = "23456789-2345-4234-8234-234567890123";
const createdAt = "2026-08-23T08:00:00+00:00";
const startedAt = "2026-08-23T08:00:02+00:00";
const finishedAt = "2026-08-23T08:00:12+00:00";

const sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

export const showcaseReadiness: ReadinessResponse = {
  status: "ready",
  checkedAt: "2026-08-23T08:00:00.000Z",
  checks: {
    database: { status: "up" },
    redis: { status: "up" },
    object_store: { status: "up" },
  },
};

export const showcaseInventory = [
  {
    documentId,
    title: "Information security policy",
    accessMode: "restricted",
    canManage: false,
    versionId: documentVersionId,
    versionNumber: 3,
    filename: "information-security-policy.pdf",
    mediaType: "application/pdf",
    sizeBytes: 1_845_248,
    versionStatus: "ready",
    generationId,
    ingestionStatus: "succeeded",
    ingestionStage: "ready",
    errorCode: null,
    createdAt,
    updatedAt: finishedAt,
  },
  {
    documentId: "34567890-3456-4345-8345-345678901234",
    title: "Vendor onboarding controls",
    accessMode: "tenant",
    canManage: false,
    versionId: "45678901-4567-4456-8456-456789012345",
    versionNumber: 1,
    filename: "vendor-onboarding-controls.docx",
    mediaType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    sizeBytes: 742_912,
    versionStatus: "ready",
    generationId: "56789012-5678-4567-8567-567890123456",
    ingestionStatus: "succeeded",
    ingestionStage: "ready",
    errorCode: null,
    createdAt: "2026-08-22T11:20:00+00:00",
    updatedAt: "2026-08-22T11:22:40+00:00",
  },
] as const;

export const showcaseReadyDocuments: ReadyDocumentVersion[] = showcaseInventory.map((document) => ({
  versionId: document.versionId,
  documentId: document.documentId,
  generationId: document.generationId,
  filename: document.filename,
  sizeBytes: document.sizeBytes,
  contentSha256: sha256,
  createdAt: document.createdAt,
}));

export const showcaseRun: AgentRunStatusResponse = {
  runId,
  tenantId,
  documentVersionId,
  taskType: "question_answer",
  publishRequested: false,
  status: "succeeded",
  graphVersion: "graph-v3",
  promptVersion: "grounded-answer-v2",
  modelProvider: "local",
  modelName: "reviewed4b",
  modelVersion: "1.0",
  modelRevision: "2026-08-20",
  fallbackTriggerCode: null,
  providerRequestCount: 1,
  providerUsageRequestCount: 1,
  promptTokens: 612,
  completionTokens: 188,
  totalTokens: 800,
  repairRequestCount: 0,
  fallbackCount: 0,
  breakerState: "closed",
  toolSchemaVersion: "tools-v1",
  currentExecutionSeq: 1,
  errorCode: null,
  createdAt,
  startedAt,
  waitingAt: null,
  finishedAt,
  cancelledAt: null,
  executions: [
    {
      executionId,
      sequence: 1,
      kind: "agent_graph",
      jobId,
      jobStatus: "succeeded",
      attempts: 1,
      maxAttempts: 3,
      cancelRequested: false,
      attemptHistory: [
        {
          attemptId,
          attemptNumber: 1,
          status: "succeeded",
          workerId: "worker-showcase-01",
          startedAt,
          heartbeatAt: finishedAt,
          finishedAt,
          errorCode: null,
        },
      ],
    },
  ],
};

export const showcaseEvents: AgentRunEventResponse[] = [
  {
    eventId: "67890123-6789-4678-8678-678901234567",
    seq: 1,
    eventType: "run.created",
    eventVersion: 1,
    publicPayload: { task_type: "question_answer", document_version_id: documentVersionId, publish_requested: false },
    createdAt,
  },
  {
    eventId: "78901234-7890-4789-8789-789012345678",
    seq: 2,
    eventType: "run.started",
    eventVersion: 1,
    publicPayload: { status: "running" },
    createdAt: startedAt,
  },
  {
    eventId: "89012345-8901-4890-8890-890123456789",
    seq: 3,
    eventType: "run.finished",
    eventVersion: 1,
    publicPayload: { status: "succeeded", refusal_reason: null },
    createdAt: finishedAt,
  },
];

export const showcaseArtifacts: AgentArtifactResponse[] = [
  {
    artifactId,
    runId,
    documentVersionId,
    kind: "answer",
    status: "draft_ready",
    contentType: "text/markdown",
    contentSha256: sha256,
    sizeBytes: 4_812,
    createdAt: finishedAt,
    verifiedAt: finishedAt,
    publishedAt: null,
  },
];

export const showcasePreview: AgentArtifactPreviewResponse = {
  artifactId,
  runId,
  documentVersionId,
  status: "draft_ready",
  contentSha256: sha256,
  schemaVersion: 1,
  taskType: "question_answer",
  answerText: "The policy requires annual access reviews, documented approval for privileged access, and remediation tracking for any exception. Evidence should be retained with the review record for audit purposes.",
  structuredFields: null,
  riskHint: "low",
  citations: [
    {
      chunkId,
      documentVersionId,
      sourceFilename: "information-security-policy.pdf",
      pageNumber: 7,
      heading: "Access governance",
      startOffset: 1420,
      endOffset: 1678,
      excerpt: "Privileged access is reviewed at least annually and each review must retain an approver, completion date, and remediation owner.",
    },
  ],
  behaviorVersions: {
    graphVersion: "graph-v3",
    promptVersion: "grounded-answer-v2",
    toolSchemaVersion: "tools-v1",
  },
};

export const showcaseRunId = runId;
export const showcaseArtifactId = artifactId;
