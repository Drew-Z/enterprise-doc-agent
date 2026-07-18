import type { UploadPartState } from "./types";

export interface UploadProgress {
  uploadedBytes: number;
  totalBytes: number;
  percent: number;
}

export function aggregateUploadProgress(parts: readonly UploadPartState[]): UploadProgress {
  const totalBytes = parts.reduce((total, part) => total + part.sizeBytes, 0);
  const uploadedBytes = parts.reduce((total, part) => {
    if (part.status === "uploaded") {
      return total + part.sizeBytes;
    }
    if (part.status === "uploading") {
      return total + Math.max(0, Math.min(part.uploadedBytes, part.sizeBytes));
    }
    return total;
  }, 0);
  return {
    uploadedBytes,
    totalBytes,
    percent: totalBytes === 0 ? 0 : (uploadedBytes / totalBytes) * 100,
  };
}
