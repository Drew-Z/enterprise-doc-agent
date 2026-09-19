import { expect, type APIRequestContext } from "@playwright/test";
import { z } from "zod";
import { control, identityProvider, type Fixture } from "./support";

const id = z.string().uuid();
const count = z.number().int().nonnegative();
export const identityEvidenceSchema = z.object({
  userCount: count,
  activeMembers: z.record(z.string(), count),
  identityProvider: z.enum(["signed", "keycloak"]).default("signed"),
  protocol: z.object({ pkceVerified: count, applicationCookiesAtIdp: count }).optional(),
  keycloak: z.object({
    version: z.literal("26.7.0"), issuer: z.string().url(), verifiedUsers: count,
    successfulCodeExchanges: count, passwordUpdates: count, capturedEmails: count,
    pkceRequired: z.boolean(), confidentialClient: z.boolean(), implicitDisabled: z.boolean(),
    passwordGrantDisabled: z.boolean(), exactCallback: z.boolean(),
    bindingCount: count, bindingsMatchKeycloak: z.boolean(),
  }).optional(),
});

export function assertIdentityProvider(state: z.infer<typeof identityEvidenceSchema>, users: number) {
  expect(state.identityProvider).toBe(identityProvider());
  if (identityProvider() === "signed") {
    expect(state.protocol).toMatchObject({ pkceVerified: users, applicationCookiesAtIdp: 0 });
    expect(state.keycloak).toBeUndefined();
  } else {
    expect(state.protocol).toBeUndefined();
    expect(state.keycloak).toMatchObject({
      version: "26.7.0", verifiedUsers: users, successfulCodeExchanges: users,
      pkceRequired: true, confidentialClient: true, implicitDisabled: true,
      passwordGrantDisabled: true, exactCallback: true, bindingsMatchKeycloak: true,
      bindingCount: users * 2, capturedEmails: users,
    });
  }
}
const snapshotSchema = identityEvidenceSchema.extend({
  tenants: z.array(z.object({ id, name: z.string(), storageUsedBytes: count, storageReservedBytes: count, storageLimitBytes: count })),
  memberships: z.array(z.object({ tenantId: id, actorId: id, role: z.string(), isActive: z.boolean() })),
  uploads: z.array(z.object({ id, tenantId: id, versionId: id.nullable(), filename: z.string(), status: z.string(), sizeBytes: count, sha256: z.string() })),
  versions: z.array(z.object({ id, tenantId: id, documentId: id, filename: z.string(), status: z.string(), sha256: z.string(), objectSha256: z.string(), contentSha256Verified: z.boolean() })),
  generations: z.array(z.object({ id, versionId: id, status: z.string(), stage: z.string(), active: z.boolean(), chunkCount: count, embeddedCount: count, errorCode: z.string().nullable() })),
  chunks: z.array(z.object({ id, versionId: id, text: z.string(), heading: z.string().nullable(), pageNumber: count.nullable(), embeddingPresent: z.boolean() })),
  jobs: z.array(z.object({ id, tenantId: id, versionId: id, status: z.string(), attempts: count, errorCode: z.string().nullable() })),
  jobAttempts: z.array(z.object({ jobId: id, status: z.string(), workerId: z.string(), errorCode: z.string().nullable() })),
  outbox: z.array(z.object({ id, tenantId: id, jobId: id, status: z.string(), attempts: count })),
  claimedEventIds: z.array(id),
  publisherRunning: z.boolean(),
  packets: z.array(z.object({ id, tenantId: id, actorId: id })),
  presalesAttempts: z.array(z.object({ id, tenantId: id, rowId: id, number: count, state: z.string(), errorCode: z.string().nullable(), providerRequestCount: count.nullable() })),
  reviews: z.array(z.object({ tenantId: id, rowId: id, actorId: id, revision: count })),
  entitlements: z.array(z.object({ tenantId: id, limit: count.nullable(), used: count, reserved: count })),
  reservations: z.array(z.object({ tenantId: id, operationId: id, state: z.string() })),
  usageEvents: z.array(z.object({ tenantId: id, operationId: id, eventType: z.string(), quantity: count, estimatedCost: z.string().nullable(), currency: z.string().nullable() })),
  audit: z.array(z.object({ tenantId: id.nullable(), action: z.string(), requestLinked: z.boolean() })),
  modelRequests: z.array(z.object({ status: count, controlledFailure: z.boolean() })),
  modelSuccesses: z.array(z.object({ requirementKey: z.string(), citation: z.object({ chunkId: id, documentVersionId: id, excerpt: z.string() }) })),
});
export type Snapshot = z.infer<typeof snapshotSchema>;

export async function snapshot(request: APIRequestContext) {
  return snapshotSchema.parse(await (await control(request, "/test/state")).json());
}

export function assertReady(value: Snapshot, fixture: Fixture, versionId: string, tenantId: string) {
  expect(value.uploads.find(item => item.versionId === versionId)).toMatchObject({ tenantId, status: "completed", filename: fixture.name, sha256: fixture.sha256, sizeBytes: fixture.sizeBytes });
  expect(value.versions.find(item => item.id === versionId)).toMatchObject({ tenantId, status: "ready", sha256: fixture.sha256, objectSha256: fixture.sha256, contentSha256Verified: true });
  expect(value.generations.find(item => item.versionId === versionId)).toMatchObject({ status: "succeeded", stage: "ready", active: true, chunkCount: 1, embeddedCount: 1, errorCode: null });
  expect(value.chunks.filter(item => item.versionId === versionId)).toEqual([expect.objectContaining({ text: fixture.excerpt, heading: fixture.heading, pageNumber: fixture.pageNumber, embeddingPresent: true })]);
  const job = value.jobs.find(item => item.versionId === versionId);
  expect(job).toMatchObject({ tenantId, status: "succeeded", attempts: 1, errorCode: null });
  expect(value.jobAttempts.find(item => item.jobId === job?.id)).toMatchObject({ status: "succeeded", workerId: expect.stringMatching(/^first-use-/), errorCode: null });
  const event = value.outbox.find(item => item.jobId === job?.id);
  expect(event).toMatchObject({ tenantId, status: "published", attempts: 1 });
  expect(value.claimedEventIds).toContain(event?.id);
}
