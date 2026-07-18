import type {
  CompleteUploadResponse,
  CreateUploadRequest,
  CreateUploadResponse,
  GetUploadResponse,
  UploadedPart,
} from "../api/schemas";
import type { HashResult } from "../hashing/protocol";

export type UploadPhase =
  | "idle"
  | "awaiting_file"
  | "hashing"
  | "creating"
  | "uploading"
  | "paused"
  | "completing"
  | "completed"
  | "failed"
  | "canceled";

export type UploadHashMode = "initial" | "parts" | "resume";

export interface UploadFileIdentity {
  filename: string;
  sizeBytes: number;
  declaredSha256: string;
}

export interface PersistedUploadSession {
  version: 1;
  sessionId: string;
  filename: string;
  sizeBytes: number;
  declaredSha256: string;
  partSizeBytes: number;
  expiresAt: string;
}

export type UploadPartStatus = "pending" | "presigning" | "uploading" | "uploaded" | "failed";

export interface UploadPartState {
  partNumber: number;
  startByte: number;
  endByte: number;
  sizeBytes: number;
  checksumSha256: string;
  status: UploadPartStatus;
  attempt: number;
  uploadedBytes: number;
  etag: string | null;
  errorCode: string | null;
}

export type UploadFailureStage = "hash" | "create" | "reconcile" | "complete" | "file_identity";

export interface UploadFailure {
  stage: UploadFailureStage;
  code: string;
  message: string;
  retryable: boolean;
}

export interface UploadMachineState {
  phase: UploadPhase;
  generation: number;
  file: File | null;
  mediaType: string | null;
  idempotencyKey: string | null;
  fileIdentity: UploadFileIdentity | null;
  session: PersistedUploadSession | null;
  hashMode: UploadHashMode | null;
  hashProcessedBytes: number;
  hashResult: HashResult | null;
  reconciling: boolean;
  parts: UploadPartState[];
  completion: CompleteUploadResponse | null;
  failure: UploadFailure | null;
}

export interface UploadPartDescriptor {
  partNumber: number;
  attempt: number;
  startByte: number;
  endByte: number;
  sizeBytes: number;
  checksumSha256: string;
}

export type UploadEffect =
  | {
      type: "hash_file";
      generation: number;
      mode: UploadHashMode;
      file: File;
      partSizeBytes: number;
    }
  | {
      type: "create_session";
      generation: number;
      request: CreateUploadRequest;
      idempotencyKey: string;
    }
  | { type: "persist_session"; session: PersistedUploadSession }
  | { type: "clear_persistence" }
  | { type: "fetch_session"; generation: number; sessionId: string }
  | {
      type: "queue_parts";
      generation: number;
      sessionId: string;
      file: File;
      parts: UploadPartDescriptor[];
    }
  | { type: "pause_scheduler"; clearQueued: true }
  | { type: "resume_scheduler" }
  | { type: "clear_scheduler" }
  | { type: "abort_hash" }
  | { type: "abort_transfers" }
  | {
      type: "complete_session";
      generation: number;
      sessionId: string;
      parts: UploadedPart[];
    }
  | { type: "abort_session"; sessionId: string };

export type UploadAction =
  | { type: "select_file"; file: File; mediaType: string; idempotencyKey: string }
  | { type: "restore_session"; session: PersistedUploadSession }
  | { type: "reselect_file"; file: File }
  | { type: "pause" }
  | { type: "resume" }
  | { type: "retry_part"; partNumber: number }
  | { type: "retry" }
  | { type: "cancel" }
  | { type: "clear" }
  | { type: "hash_progress"; generation: number; processedBytes: number; totalBytes: number }
  | { type: "hash_succeeded"; generation: number; result: HashResult }
  | { type: "hash_failed"; generation: number; code: string; message: string }
  | { type: "session_created"; generation: number; session: CreateUploadResponse }
  | { type: "session_create_failed"; generation: number; code: string; message: string }
  | { type: "session_reconciled"; generation: number; session: GetUploadResponse }
  | { type: "session_reconcile_failed"; generation: number; code: string; message: string }
  | { type: "part_presign_started"; generation: number; partNumber: number; attempt: number }
  | { type: "part_upload_started"; generation: number; partNumber: number; attempt: number }
  | {
      type: "part_progress";
      generation: number;
      partNumber: number;
      attempt: number;
      uploadedBytes: number;
    }
  | {
      type: "part_uploaded";
      generation: number;
      partNumber: number;
      attempt: number;
      etag: string;
    }
  | {
      type: "part_failed";
      generation: number;
      partNumber: number;
      attempt: number;
      code: string;
    }
  | { type: "complete_succeeded"; generation: number; result: CompleteUploadResponse }
  | { type: "complete_failed"; generation: number; code: string; message: string };

export interface UploadTransition {
  accepted: boolean;
  state: UploadMachineState;
  effects: UploadEffect[];
}
