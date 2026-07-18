import { afterEach, describe, expect, it } from "vitest";

import {
  createUploadRecoveryStore,
  createUploadTokenStore,
  persistedUploadSessionSchema,
  UPLOAD_RECOVERY_STORAGE_KEY,
  UPLOAD_TOKEN_STORAGE_KEY,
  UploadPersistenceError,
} from "./persistence";

const session = {
  version: 1 as const,
  sessionId: "11111111-1111-4111-8111-111111111111",
  filename: "contract.pdf",
  sizeBytes: 5,
  declaredSha256: "a".repeat(64),
  partSizeBytes: 3,
  expiresAt: "2026-07-18T00:00:00Z",
};

afterEach(() => {
  sessionStorage.clear();
});

describe("upload recovery persistence", () => {
  it("round-trips only the strict safe-field whitelist", () => {
    const store = createUploadRecoveryStore(sessionStorage);
    store.save(session);
    expect(store.load()).toEqual(session);

    const raw = sessionStorage.getItem(UPLOAD_RECOVERY_STORAGE_KEY);
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw as string) as Record<string, unknown>;
    expect(Object.keys(parsed).sort()).toEqual([
      "declaredSha256",
      "expiresAt",
      "filename",
      "partSizeBytes",
      "sessionId",
      "sizeBytes",
      "version",
    ]);
    expect(JSON.stringify(parsed)).not.toMatch(/token|authorization|url|header|objectKey|uploadId/i);
  });

  it.each([
    { ...session, version: 2 },
    { ...session, signedUrl: "http://object.test/secret" },
    { ...session, objectKey: "m1/uploads/secret" },
    { ...session, sessionId: "not-a-uuid" },
  ])("rejects invalid versions, secrets, extra fields, and malformed values", (value) => {
    expect(persistedUploadSessionSchema.safeParse(value).success).toBe(false);
  });

  it("discards invalid stored JSON and records", () => {
    const store = createUploadRecoveryStore(sessionStorage);
    sessionStorage.setItem(UPLOAD_RECOVERY_STORAGE_KEY, "{bad-json");
    expect(store.load()).toBeNull();
    expect(sessionStorage.getItem(UPLOAD_RECOVERY_STORAGE_KEY)).toBeNull();

    sessionStorage.setItem(UPLOAD_RECOVERY_STORAGE_KEY, JSON.stringify({ ...session, token: "secret" }));
    expect(store.load()).toBeNull();
    expect(sessionStorage.getItem(UPLOAD_RECOVERY_STORAGE_KEY)).toBeNull();
  });

  it("stores the local token under an isolated key", () => {
    const recoveryStore = createUploadRecoveryStore(sessionStorage);
    const tokenStore = createUploadTokenStore(sessionStorage);
    recoveryStore.save(session);
    tokenStore.save("header.payload.signature");

    expect(tokenStore.load()).toBe("header.payload.signature");
    expect(sessionStorage.getItem(UPLOAD_RECOVERY_STORAGE_KEY)).not.toContain("header.payload.signature");
    expect(sessionStorage.getItem(UPLOAD_TOKEN_STORAGE_KEY)).toBe("header.payload.signature");
    expect(() => tokenStore.save("token with spaces")).toThrow(UploadPersistenceError);
  });
});
