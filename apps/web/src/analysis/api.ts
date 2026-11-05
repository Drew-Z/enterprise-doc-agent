import { z, type ZodType } from "zod";
import { authenticatedFetch, type ApiCredential } from "../auth/transport";
import { errorResponseSchema } from "../agent/api/schemas";

const uuid = z.string().uuid();
const hash = z.string().regex(/^[a-f0-9]{64}$/);
export const locator = z.object({
  kind: z.enum(["txt", "pdf", "docx", "xlsx"]),
  page: z.number().int().positive().nullable(), line: z.number().int().positive().nullable(),
  part: z.string().nullable(), paragraph: z.number().int().positive().nullable(), table: z.string().nullable(),
  row: z.number().int().positive().nullable(), column: z.number().int().positive().nullable(),
  rowSpan: z.number().int().positive(), columnSpan: z.number().int().positive(),
  sheet: z.string().nullable(), cell: z.string().nullable(), mergedRange: z.string().nullable(), hidden: z.boolean(),
}).strict();
const issue = z.object({ code: z.string(), locator }).strict();
export const fieldSchema = z.object({
  id: z.string().regex(/^[a-z][a-z0-9_]{0,63}$/), name: z.string().min(1).max(160), question: z.string().min(1).max(1000),
  valueType: z.enum(["text", "boolean", "decimal", "money", "date", "duration", "enum", "list"]),
  parameterIds: z.array(z.string().regex(/^[a-z][a-z0-9_]{0,63}$/)).max(8).default([]),
  upstreamFieldIds: z.array(z.string().regex(/^[a-z][a-z0-9_]{0,63}$/)).max(8).default([]),
}).strict();
export const parameterSchema = z.object({
  id: z.string().regex(/^[a-z][a-z0-9_]{0,63}$/), name: z.string().min(1).max(160),
  value: z.string().min(1).max(2000), unit: z.string().min(1).max(32).nullable(),
}).strict();
export const specSchema = z.object({
  schemaVersion: z.enum(["analysis.spec.v1", "analysis.spec.v2"]), commonRequirements: z.array(uuid).max(10),
  objects: z.array(z.object({ id: z.string(), name: z.string().min(1).max(160), sourceVersionIds: z.array(uuid).max(10) }).strict()).min(1).max(3),
  fields: z.array(fieldSchema).min(1).max(10), parameters: z.array(parameterSchema).max(8).default([]),
}).strict();
export const projectSchema = z.object({
  id: uuid, title: z.string(), revision: z.number().int().positive(), spec: specSchema, createdAt: z.string().datetime({ offset: true }),
}).strict();
const sourceSchema = z.object({
  documentVersionId: uuid, generationId: uuid, objectId: z.string().nullable(), filename: z.string(),
  sourceSha256: hash, structureSha256: hash, parserVersion: z.literal(2),
  coverage: z.enum(["complete", "partial", "unknown"]), blockCount: z.number().int().nonnegative(), issues: z.array(issue),
}).strict();
export const snapshotSchema = z.object({
  id: uuid, projectId: uuid, projectRevision: z.number().int().positive(), sourceManifestHash: hash,
  payload: z.object({ schemaVersion: z.enum(["analysis.sources.v1", "analysis.sources.v2"]), title: z.string(), spec: specSchema, sources: z.array(sourceSchema) }).strict(),
  createdAt: z.string().datetime({ offset: true }),
}).strict();
const blockSchema = z.object({
  index: z.number().int().nonnegative(), blockId: hash, kind: z.enum(["paragraph", "heading", "table_cell"]),
  text: z.string(), originalText: z.string(), startOffset: z.number().int().nonnegative(), endOffset: z.number().int().nonnegative(),
  contentSha256: hash, locator, heading: z.string().nullable(), formula: z.string().nullable(),
  cachedValue: z.string().nullable(), numberFormat: z.string().nullable(),
}).strict();
export const blocksSchema = z.object({ source: sourceSchema, blocks: z.array(blockSchema), nextOffset: z.number().int().nonnegative().nullable() }).strict();
export const capabilitiesSchema = z.object({
  admissionEnabled: z.boolean(), structuredUploadsEnabled: z.boolean(), executionEnabled: z.boolean(),
  maxSourceFiles: z.number().int().positive(), maxObjects: z.number().int().positive(), maxFields: z.number().int().positive(),
  maxFileBytes: z.number().int().positive(), template: z.array(fieldSchema),
}).strict();
export type AnalysisProject = z.infer<typeof projectSchema>;
export type AnalysisSpec = z.infer<typeof specSchema>;
export type AnalysisField = z.infer<typeof fieldSchema>;
export type AnalysisParameter = z.infer<typeof parameterSchema>;
export type AnalysisSnapshot = z.infer<typeof snapshotSchema>;
export type SourceLocator = z.infer<typeof locator>;

export class AnalysisApiError extends Error {
  constructor(readonly code: string, readonly requestId: string | null) {
    super("Analysis request failed.");
  }
}

export class AnalysisClient {
  constructor(private readonly credential: ApiCredential) {}

  protected async json<T>(path: string, schema: ZodType<T>, init: RequestInit = {}): Promise<T> {
    const response = await authenticatedFetch(`${(import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "")}/api/analysis${path}`, this.credential, {
      ...init, headers: { Accept: "application/json", ...init.headers },
    });
    if (!response.ok) {
      const error = errorResponseSchema.safeParse(await response.json().catch(() => null));
      throw new AnalysisApiError(error.success ? error.data.error.code : "analysis_unavailable", error.success ? error.data.error.requestId : null);
    }
    return schema.parse(await response.json());
  }

  capabilities(signal?: AbortSignal) { return this.json("/capabilities", capabilitiesSchema, { signal }); }
  projects(signal?: AbortSignal) { return this.json("/projects", z.array(projectSchema), { signal }); }
  snapshots(projectId: string, signal?: AbortSignal) { return this.json(`/projects/${uuid.parse(projectId)}/snapshots`, z.array(snapshotSchema), { signal }); }
  create(title: string, spec: AnalysisSpec) {
    return this.json("/projects", projectSchema, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title, spec: specSchema.parse(spec) }) });
  }
  update(project: AnalysisProject, title: string, spec: AnalysisSpec) {
    return this.json(`/projects/${uuid.parse(project.id)}`, projectSchema, { method: "PATCH", headers: { "Content-Type": "application/json", "If-Match": `"${project.revision}"` }, body: JSON.stringify({ title, spec: specSchema.parse(spec) }) });
  }
  freeze(project: AnalysisProject) {
    return this.json(`/projects/${uuid.parse(project.id)}/snapshots`, snapshotSchema, { method: "POST", headers: { "If-Match": `"${project.revision}"` } });
  }
  blocks(snapshotId: string, versionId: string, offset: number, signal?: AbortSignal) {
    return this.json(`/snapshots/${uuid.parse(snapshotId)}/sources/${uuid.parse(versionId)}/blocks?offset=${offset}&limit=50`, blocksSchema, { signal });
  }
}
