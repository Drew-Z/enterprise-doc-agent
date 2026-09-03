import type { AuditEvent } from "./auditApi";
import type { AuditArchiveBatch, AuditLegalHold, AuditRetentionPlan, AuditRetentionPolicy, AuditRetentionPreview } from "./auditGovernanceApi";

const tenantId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const documentId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const documentVersionId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const runId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
const jobId = "ffffffff-ffff-4fff-8fff-ffffffffffff";
const actorId = "99999999-9999-4999-8999-999999999999";
const approvalId = "aaaaaaaa-bbbb-4aaa-8bbb-aaaaaaaaaaaa";
const artifactId = "12345678-1234-4234-8234-123456789012";

function event(
  eventId: string,
  occurredAt: string,
  action: string,
  resourceType: string,
  resourceId: string | null,
  metadata: Record<string, unknown>,
): AuditEvent {
  return {
    eventId,
    tenantId,
    actorId,
    action,
    resourceType,
    resourceId,
    occurredAt,
    requestId: "req-showcase-7f2c",
    correlationId: "corr-ingest-agent-42",
    metadata,
    schemaVersion: 1,
  };
}

export const showcaseAuditEvents: AuditEvent[] = [
  event("01000000-0000-4000-8000-000000000001", "2026-08-23T08:00:00+00:00", "document.upload_completed", "document", documentId, { filename: "information-security-policy.pdf", size_bytes: 1845248 }),
  event("01000000-0000-4000-8000-000000000002", "2026-08-23T08:00:04+00:00", "job.created", "job", jobId, { job_type: "document.ingest", status: "pending" }),
  event("01000000-0000-4000-8000-000000000003", "2026-08-23T08:00:08+00:00", "job.succeeded", "job", jobId, { job_type: "document.ingest", status: "succeeded" }),
  event("01000000-0000-4000-8000-000000000004", "2026-08-23T08:00:10+00:00", "agent_run.created", "agent_run", runId, { task_type: "question_answer", document_version_id: documentVersionId }),
  event("01000000-0000-4000-8000-000000000005", "2026-08-23T08:00:12+00:00", "agent_run.waiting_approval", "agent_run", runId, { approval_id: approvalId, status: "waiting_approval" }),
  event("01000000-0000-4000-8000-000000000006", "2026-08-23T08:00:20+00:00", "approval.approved", "approval", approvalId, { operation: "publish_artifact", decided_by: actorId }),
  event("01000000-0000-4000-8000-000000000007", "2026-08-23T08:00:21+00:00", "artifact.published", "artifact", artifactId, { content_sha256: "0123456789abcdef...", verified: true }),
  event("01000000-0000-4000-8000-000000000008", "2026-08-23T08:00:24+00:00", "agent_run.finished", "agent_run", runId, { status: "succeeded", citations: 1 }),
];

export const showcaseAuditRetentionPolicy: AuditRetentionPolicy = {
  tenantId,
  retentionDays: 365,
  isEnabled: false,
  updatedBy: null,
};

export const showcaseAuditRetentionPreview: AuditRetentionPreview = {
  cutoffAt: null,
  eligibleEventCount: 0,
  protectedEventCount: 0,
};

export const showcaseAuditRetentionPlan: AuditRetentionPlan = {
  policy: showcaseAuditRetentionPolicy,
  cutoffAt: null,
  eligibleEventCount: 0,
  protectedEventCount: 0,
  eligibleEventIds: [],
  fingerprint: "f".repeat(64),
};

export const showcaseAuditArchiveBatches: AuditArchiveBatch[] = [];

export const showcaseAuditLegalHolds: AuditLegalHold[] = [
  {
    holdId: "01000000-0000-4000-8000-000000000101",
    tenantId,
    name: "Q3 audit review",
    reason: "Preserve governance events while the quarterly control review is open.",
    resourceType: null,
    resourceId: null,
    startsAt: "2026-08-01T00:00:00+00:00",
    expiresAt: "2026-09-30T23:59:59+00:00",
    releasedAt: null,
    createdBy: actorId,
    releasedBy: null,
  },
];
