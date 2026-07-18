import type { UploadFileIdentity } from "./state/types";

export type FileIdentityMismatch = "filename" | "size" | "sha256";

export function compareFileMetadata(expected: UploadFileIdentity, file: File): FileIdentityMismatch | null {
  if (file.name !== expected.filename) {
    return "filename";
  }
  if (file.size !== expected.sizeBytes) {
    return "size";
  }
  return null;
}

export function compareHashedFileIdentity(
  expected: UploadFileIdentity,
  actual: UploadFileIdentity,
): FileIdentityMismatch | null {
  if (actual.filename !== expected.filename) {
    return "filename";
  }
  if (actual.sizeBytes !== expected.sizeBytes) {
    return "size";
  }
  if (actual.declaredSha256 !== expected.declaredSha256) {
    return "sha256";
  }
  return null;
}
