export const HASH_PROTOCOL_VERSION = 1 as const;

export type HashFailureCode = "aborted" | "invalid_request" | "read_failed" | "hash_failed";

export interface HashPartResult {
  partNumber: number;
  sizeBytes: number;
  checksumSha256: string;
}

export interface HashResult {
  wholeSha256: string;
  parts: HashPartResult[];
}

export interface HashStartMessage {
  version: typeof HASH_PROTOCOL_VERSION;
  type: "start";
  jobId: string;
  file: File;
  partSizeBytes: number;
  readChunkSizeBytes?: number;
}

export interface HashCancelMessage {
  version: typeof HASH_PROTOCOL_VERSION;
  type: "cancel";
  jobId: string;
}

export type HashWorkerRequest = HashStartMessage | HashCancelMessage;

export interface HashProgressMessage {
  version: typeof HASH_PROTOCOL_VERSION;
  type: "progress";
  jobId: string;
  processedBytes: number;
  totalBytes: number;
}

export interface HashCompletedMessage {
  version: typeof HASH_PROTOCOL_VERSION;
  type: "completed";
  jobId: string;
  result: HashResult;
}

export interface HashFailedMessage {
  version: typeof HASH_PROTOCOL_VERSION;
  type: "failed";
  jobId: string;
  error: {
    code: HashFailureCode;
    message: string;
  };
}

export type HashWorkerResponse = HashProgressMessage | HashCompletedMessage | HashFailedMessage;

const HASH_FAILURE_CODES = new Set<HashFailureCode>([
  "aborted",
  "invalid_request",
  "read_failed",
  "hash_failed",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isJobId(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= 128;
}

function isCanonicalSha256Base64(value: unknown): value is string {
  if (typeof value !== "string" || !/^[A-Za-z0-9+/]{43}=$/.test(value)) {
    return false;
  }
  try {
    const decoded = atob(value);
    return decoded.length === 32 && btoa(decoded) === value;
  } catch {
    return false;
  }
}

function isHashPartResult(value: unknown, expectedPartNumber: number): value is HashPartResult {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["partNumber", "sizeBytes", "checksumSha256"]) &&
    value.partNumber === expectedPartNumber &&
    isPositiveInteger(value.sizeBytes) &&
    isCanonicalSha256Base64(value.checksumSha256)
  );
}

function isHashResult(value: unknown): value is HashResult {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["wholeSha256", "parts"]) &&
    typeof value.wholeSha256 === "string" &&
    /^[0-9a-f]{64}$/.test(value.wholeSha256) &&
    Array.isArray(value.parts) &&
    value.parts.length > 0 &&
    value.parts.length <= 10_000 &&
    value.parts.every((part, index) => isHashPartResult(part, index + 1))
  );
}

export function parseHashWorkerRequest(value: unknown): HashWorkerRequest | null {
  if (!isRecord(value) || value.version !== HASH_PROTOCOL_VERSION || !isJobId(value.jobId)) {
    return null;
  }

  if (value.type === "cancel") {
    return hasExactKeys(value, ["version", "type", "jobId"]) ? (value as unknown as HashCancelMessage) : null;
  }

  if (value.type !== "start") {
    return null;
  }

  const allowedKeys = value.readChunkSizeBytes === undefined
    ? ["version", "type", "jobId", "file", "partSizeBytes"]
    : ["version", "type", "jobId", "file", "partSizeBytes", "readChunkSizeBytes"];
  if (
    !hasExactKeys(value, allowedKeys) ||
    !(value.file instanceof File) ||
    value.file.size <= 0 ||
    !isPositiveInteger(value.partSizeBytes) ||
    (value.readChunkSizeBytes !== undefined && !isPositiveInteger(value.readChunkSizeBytes))
  ) {
    return null;
  }

  return value as unknown as HashStartMessage;
}

export function isHashWorkerResponse(value: unknown): value is HashWorkerResponse {
  if (
    !isRecord(value) ||
    value.version !== HASH_PROTOCOL_VERSION ||
    !isJobId(value.jobId) ||
    typeof value.type !== "string"
  ) {
    return false;
  }

  if (value.type === "progress") {
    return (
      hasExactKeys(value, ["version", "type", "jobId", "processedBytes", "totalBytes"]) &&
      isNonNegativeInteger(value.processedBytes) &&
      isPositiveInteger(value.totalBytes) &&
      value.processedBytes <= value.totalBytes
    );
  }
  if (value.type === "completed") {
    return hasExactKeys(value, ["version", "type", "jobId", "result"]) && isHashResult(value.result);
  }
  if (value.type === "failed") {
    return (
      hasExactKeys(value, ["version", "type", "jobId", "error"]) &&
      isRecord(value.error) &&
      hasExactKeys(value.error, ["code", "message"]) &&
      typeof value.error.code === "string" &&
      HASH_FAILURE_CODES.has(value.error.code as HashFailureCode) &&
      typeof value.error.message === "string" &&
      value.error.message.length > 0
    );
  }
  return false;
}
