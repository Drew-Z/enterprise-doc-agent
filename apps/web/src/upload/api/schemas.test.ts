import { describe, expect, it } from "vitest";

import {
  createUploadResponseSchema,
  getUploadResponseSchema,
  presignPartResponseSchema,
  sha256Base64Schema,
} from "./schemas";

const sessionId = "11111111-1111-4111-8111-111111111111";
const baseResponse = {
  sessionId,
  status: "active",
  filename: "contract.pdf",
  extension: ".pdf",
  mediaType: "application/pdf",
  sizeBytes: 5,
  declaredSha256: "a".repeat(64),
  partSizeBytes: 5,
  expectedPartCount: 1,
  expiresAt: "2026-07-18T00:00:00Z",
};

describe("upload API schemas", () => {
  it("accepts exact camelCase create and session responses", () => {
    expect(createUploadResponseSchema.parse({ ...baseResponse, replayed: false })).toMatchObject({ sessionId });
    expect(getUploadResponseSchema.parse({ ...baseResponse, uploadedParts: [] })).toMatchObject({ uploadedParts: [] });
  });

  it.each([
    { ...baseResponse, replayed: false, session_id: sessionId },
    { ...baseResponse, replayed: false, unexpected: true },
    { ...baseResponse, replayed: false, sessionId: "not-a-uuid" },
    { ...baseResponse, replayed: false, status: "unknown" },
  ])("rejects aliases, unknown fields, and invalid primitives", (value) => {
    expect(createUploadResponseSchema.safeParse(value).success).toBe(false);
  });

  it("accepts only canonical base64 SHA-256 values", () => {
    const canonical = btoa(String.fromCharCode(...new Uint8Array(32)));
    expect(sha256Base64Schema.safeParse(canonical).success).toBe(true);
    expect(sha256Base64Schema.safeParse("A".repeat(42) + "B=").success).toBe(false);
    expect(sha256Base64Schema.safeParse("not-base64").success).toBe(false);
  });

  it("validates presign URLs, headers, and exact response fields", () => {
    const checksum = btoa(String.fromCharCode(...new Uint8Array(32)));
    expect(
      presignPartResponseSchema.parse({
        partNumber: 1,
        sizeBytes: 5,
        checksumSha256: checksum,
        url: "http://127.0.0.1:9000/bucket/key?signature=secret",
        headers: { "x-amz-checksum-sha256": checksum },
        expiresInSeconds: 300,
      }).headers,
    ).toEqual({ "x-amz-checksum-sha256": checksum });
    expect(
      presignPartResponseSchema.safeParse({
        partNumber: 1,
        sizeBytes: 5,
        checksumSha256: checksum,
        url: "javascript:alert(1)",
        headers: {},
        expiresInSeconds: 300,
      }).success,
    ).toBe(false);
  });
});
