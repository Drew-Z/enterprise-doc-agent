import { describe, expect, it } from "vitest";
import { initialUploadState, reduceUpload } from "./reducer";
import { persistedUploadSessionSchema } from "../persistence";
import type { CreateUploadResponse } from "../api/schemas";

const file = new File(["hello"], "test.txt", { type: "text/plain" });
const hash = { wholeSha256: "a".repeat(64), parts: [{ partNumber: 1, sizeBytes: 5, checksumSha256: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" }] };
const response: CreateUploadResponse = {
  sessionId: "11111111-1111-4111-8111-111111111111", status: "active",
  filename: file.name, extension: ".txt", mediaType: file.type, sizeBytes: 5,
  declaredSha256: hash.wholeSha256, partSizeBytes: 5, expectedPartCount: 1,
  expiresAt: "2026-10-06T00:00:00Z", replayed: false, transport: "single_put",
};
function creating() {
  const selected = reduceUpload(initialUploadState, { type: "select_file", file, mediaType: file.type, idempotencyKey: "same-key" });
  return reduceUpload(selected.state, { type: "hash_succeeded", generation: 1, result: hash });
}
describe("single PUT upload state", () => {
  it("keeps the selected mode on create retry", () => {
    const started = creating();
    expect(started.effects[0]).toMatchObject({ request: { transport: "single_put" } });
    const failed = reduceUpload(started.state, { type: "session_create_failed", generation: 1, code: "network", message: "retry" });
    expect(reduceUpload(failed.state, { type: "retry" }).effects).toEqual(started.effects);
  });
  it("persists mode and carries it through transfer and completion", () => {
    const created = reduceUpload(creating().state, { type: "session_created", generation: 1, session: response });
    const saved = persistedUploadSessionSchema.parse(created.state.session);
    expect(saved.transport).toBe("single_put");
    let result = reduceUpload(created.state, { type: "hash_succeeded", generation: 1, result: hash });
    expect(result.effects).toContainEqual(expect.objectContaining({ type: "queue_parts", transport: "single_put" }));
    for (const type of ["part_presign_started", "part_upload_started"] as const) {
      result = reduceUpload(result.state, { type, generation: 1, partNumber: 1, attempt: 1 });
    }
    result = reduceUpload(result.state, { type: "part_uploaded", generation: 1, partNumber: 1, attempt: 1, etag: "verified" });
    expect(result.effects).toContainEqual(expect.objectContaining({ type: "complete_session", transport: "single_put" }));
  });
  it("rejects a changed transport after refresh", () => {
    const created = reduceUpload(creating().state, { type: "session_created", generation: 1, session: response });
    const saved = persistedUploadSessionSchema.parse(created.state.session);
    const restored = reduceUpload(initialUploadState, { type: "restore_session", session: saved });
    const result = reduceUpload(restored.state, { type: "session_reconciled", generation: 1, session: { ...response, transport: "multipart", uploadedParts: [] } });
    expect(result.state.failure?.code).toBe("session_identity_mismatch");
    expect(result.effects).toEqual([]);
  });
  it("rejects a multi-part plan claiming single PUT", () => {
    const result = reduceUpload(creating().state, { type: "session_created", generation: 1, session: { ...response, partSizeBytes: 3, expectedPartCount: 2 } });
    expect(result.state.failure?.code).toBe("session_identity_mismatch");
  });
  it("keeps legacy recovery records valid and rejects secret fields", () => {
    const created = reduceUpload(creating().state, { type: "session_created", generation: 1, session: response });
    const saved = persistedUploadSessionSchema.parse(created.state.session);
    const legacy = { ...saved };
    delete legacy.transport;
    expect(persistedUploadSessionSchema.parse(legacy)).toEqual(legacy);
    expect(persistedUploadSessionSchema.safeParse({ ...saved, signedUrl: "secret" }).success).toBe(false);
  });
});
