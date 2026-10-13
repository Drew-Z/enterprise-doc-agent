import { z } from "zod";
import { AnalysisClient, locator, snapshotSchema } from "./api";

const uuid = z.string().uuid();
const count = z.number().int().nonnegative();
const citation = z.object({ evidenceId: z.string(), quote: z.string() }).strict();
const calculation = z.object({
  id: z.string(), formulaVersion: z.literal("analysis.calculation.v1"),
  operation: z.enum(["sum", "product", "unit_conversion", "explicit_period_total"]),
  expression: z.string(), operands: z.array(z.object({ name: z.string(), value: z.string(), unit: z.string(), source: z.string() }).strict()),
  result: z.string(), resultUnit: z.string(), rounding: z.enum(["half_even", "half_up", "down"]), decimalPlaces: count,
}).strict();
const value = z.union([z.string(), z.boolean(), z.object({ amount: z.string(), currency: z.string() }).strict(), z.object({ amount: z.string(), unit: z.enum(["hour", "day", "week", "month", "year"]), startsWhen: z.string() }).strict(), z.array(z.string()), z.null()]);
const hash = z.string().regex(/^[0-9a-f]{64}$/);
const review = z.object({
  id: uuid, cellRevisionId: uuid, revision: z.number().int().positive(), contentHash: hash,
  decision: z.enum(["approved", "changes_requested", "rejected"]), reason: z.string(), actorId: uuid, createdAt: z.string().datetime({ offset: true }),
}).strict();
const result = z.object({
  action: z.enum(["final", "abstain", "need_input"]), value,
  evidenceState: z.enum(["supported", "conflicting", "insufficient", "unreadable"]),
  citations: z.array(citation), conditions: z.array(z.object({ text: z.string(), citations: z.array(citation) }).strict()),
  reason: z.string(), missingParameters: z.array(z.string()), calculationId: z.string().nullable(), calculations: z.array(calculation), valueOrigin: z.enum(["document_evidence", "user_provided"]).default("document_evidence"), comparisonState: z.literal("not_comparable"), reviewState: z.enum(["unreviewed", "approved", "changes_requested"]),
  evidence: z.array(z.object({ evidenceId: z.string(), documentVersionId: uuid, generationId: uuid, blockId: z.string(), blockHash: z.string(), quote: z.string(), quoteHash: z.string(), locator, filename: z.string() }).strict()),
}).strict();
export const runSchema = z.object({
  id: uuid, projectId: uuid, snapshotId: uuid, status: z.enum(["queued", "running", "waiting_input", "completed", "partial", "failed", "cancelled", "expired"]),
  totalUnits: count.positive(), finishedUnits: count, dispatches: count, maxDispatches: count, executionMode: z.enum(["fixed", "agent"]),
  createdAt: z.string().datetime({ offset: true }), deadlineAt: z.string().datetime({ offset: true }), cancelRequested: z.boolean(),
  baseRunId: uuid.nullable().default(null), reusedUnits: count.default(0), affectedUnits: count.default(0),
}).strict();
export const matrixSchema = z.object({ run: runSchema, snapshot: snapshotSchema, units: z.array(z.object({
  id: uuid, objectId: z.string(), fieldId: z.string(), status: z.enum(["queued", "running", "waiting_input", "succeeded", "failed", "cancelled", "expired"]),
  dispatches: count, errorCode: z.string().nullable(), result: result.nullable(),
  search: z.object({ windows: z.array(z.object({ id: z.string(), documentVersionId: uuid, generationId: uuid, blockId: z.string(), blockHash: z.string(), text: z.string(), locator, filename: z.string(), coverage: z.enum(["complete", "partial", "unknown"]), formulaUnverified: z.boolean() }).strict()), searchedVersions: z.array(uuid), searchedBlocks: count, returnedBlocks: count, coverage: z.enum(["complete", "partial", "unknown"]), exhaustiveRead: z.literal(false) }).strict().nullable(),
  inputRequest: z.object({ id: uuid, unitId: uuid, revision: z.number().int().positive(), parameters: z.array(z.object({ name: z.string(), unit: z.string().nullable() }).strict()).min(1).max(8), state: z.enum(["open", "submitted", "cancelled"]), canResume: z.boolean() }).strict().nullable(),
  currentRevision: z.number().int().positive().nullable().optional(), currentRevisionId: uuid.nullable().optional(), currentContentHash: hash.nullable().optional(), authorType: z.enum(["model", "user"]).nullable().optional(), review: review.nullable().optional(),
  freshness: z.enum(["current", "stale"]).default("current"), invalidationReasons: z.array(z.string()).default([]), reusedFromUnitId: uuid.nullable().default(null),
}).strict()) }).strict();
export type Matrix = z.infer<typeof matrixSchema>;
export type ExecutionMode = "fixed" | "agent";
export type InputSubmission = { requestId: string; expectedRevision: number; values: Record<string, string> };

export const cellRevisionSchema = z.object({
  id: uuid, unitId: uuid, revision: z.number().int().positive(), result, contentHash: hash,
  authorType: z.enum(["model", "user"]), actorId: uuid.nullable(), reason: z.string(), createdAt: z.string().datetime({ offset: true }), supersedesId: uuid.nullable(), review: review.nullable(),
}).strict();
export const artifactSchema = z.object({
  id: uuid, runId: uuid, mode: z.enum(["draft", "reviewed"]), status: z.enum(["writing", "ready", "approved", "failed"]), format: z.literal("xlsx"),
  matrixRevision: z.number().int().positive(), matrixHash: hash, sourceManifestHash: hash, cellRevisionIds: z.array(uuid), objectBucket: z.string(), objectKey: z.string(), filename: z.string(), contentType: z.string(), contentHash: hash.nullable(), sizeBytes: z.number().int().positive().nullable(), createdAt: z.string().datetime({ offset: true }),
}).strict();
const approvalSchema = z.object({
  id: uuid, artifactId: uuid, artifactHash: hash, sourceManifestHash: hash, matrixHash: hash, operation: z.literal("deliver"), decision: z.enum(["approved", "rejected"]), reason: z.string(), actorId: uuid, expiresAt: z.string().datetime({ offset: true }), decidedAt: z.string().datetime({ offset: true }), consumedAt: z.string().datetime({ offset: true }).nullable(),
}).strict();
const downloadSchema = z.object({ artifactId: uuid, filename: z.string(), contentType: z.string(), contentHash: hash, sizeBytes: z.number().int().positive(), url: z.string().url(), expiresInSeconds: z.number().int().positive() }).strict();
export type AnalysisArtifact = z.infer<typeof artifactSchema>;

export class AnalysisExecutionClient extends AnalysisClient {
  runs(projectId: string, signal?: AbortSignal) { return this.json(`/projects/${uuid.parse(projectId)}/runs`, z.array(runSchema), { signal }); }
  start(projectId: string, snapshotId: string, key: string, executionMode: ExecutionMode, baseRunId?: string) { return this.json(`/projects/${uuid.parse(projectId)}/runs`, runSchema, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key }, body: JSON.stringify({ snapshotId: uuid.parse(snapshotId), executionMode, baseRunId: baseRunId ? uuid.parse(baseRunId) : null }) }); }
  matrix(runId: string, signal?: AbortSignal) { return this.json(`/runs/${uuid.parse(runId)}/matrix`, matrixSchema, { signal }); }
  submitInputs(runId: string, submissions: InputSubmission[]) { return this.json(`/runs/${uuid.parse(runId)}/inputs`, runSchema, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ submissions }) }); }
  cancel(runId: string) { return this.json(`/runs/${uuid.parse(runId)}/cancel`, runSchema, { method: "POST" }); }
  revisions(unitId: string, signal?: AbortSignal) { return this.json(`/cells/${uuid.parse(unitId)}/revisions`, z.array(cellRevisionSchema), { signal }); }
  revise(unitId: string, revision: number, key: string, body: { action: "final" | "abstain"; value: unknown; evidenceState: "insufficient" | "conflicting" | "unreadable"; reason: string; valueOrigin: "user_provided" }) { return this.json(`/cells/${uuid.parse(unitId)}/revisions`, cellRevisionSchema, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key, "If-Match": `"${revision}"` }, body: JSON.stringify(body) }); }
  review(unitId: string, key: string, body: { revision: number; contentHash: string; decision: "approved" | "changes_requested" | "rejected"; reason: string }) { return this.json(`/cells/${uuid.parse(unitId)}/reviews`, review, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key }, body: JSON.stringify(body) }); }
  export(runId: string, mode: "draft" | "reviewed", key: string) { return this.json(`/runs/${uuid.parse(runId)}/exports`, artifactSchema, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key }, body: JSON.stringify({ mode }) }); }
  artifacts(runId: string, signal?: AbortSignal) { return this.json(`/runs/${uuid.parse(runId)}/artifacts`, z.array(artifactSchema), { signal }); }
  approve(artifact: AnalysisArtifact, key: string, reason: string) { return this.json(`/artifacts/${artifact.id}/approvals`, approvalSchema, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key }, body: JSON.stringify({ artifactHash: artifact.contentHash, sourceManifestHash: artifact.sourceManifestHash, matrixHash: artifact.matrixHash, operation: "deliver", decision: "approved", reason, expiresAt: new Date(Date.now() + 30 * 60_000).toISOString() }) }); }
  download(artifactId: string) { return this.json(`/artifacts/${uuid.parse(artifactId)}/download`, downloadSchema); }
}
