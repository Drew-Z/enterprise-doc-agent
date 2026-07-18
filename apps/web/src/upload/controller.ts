import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  UploadApiError,
  UploadApiProtocolError,
  UploadAuthenticationError,
  UploadNetworkError,
} from "./api/client";
import type {
  CompleteUploadRequest,
  CompleteUploadResponse,
  CreateUploadRequest,
  CreateUploadResponse,
  GetUploadResponse,
  PresignPartRequest,
  PresignPartResponse,
} from "./api/schemas";
import { HashWorkerClientError, type HashJob, type StartHashJobOptions } from "./hashing/client";
import {
  createUploadRecoveryStore,
  createUploadTokenStore,
  UploadPersistenceError,
} from "./persistence";
import { initialUploadState, reduceUpload } from "./state/reducer";
import { PartUploadScheduler, type ScheduledPartTask } from "./state/scheduler";
import type { UploadAction, UploadEffect, UploadMachineState } from "./state/types";
import {
  XhrUploadError,
  type UploadPartWithXhrOptions,
  type XhrUploadHandle,
} from "./transfer/xhrUploadPart";

export interface UploadApiPort {
  createSession(
    request: CreateUploadRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<CreateUploadResponse>;
  getSession(sessionId: string, signal?: AbortSignal): Promise<GetUploadResponse>;
  presignPart(
    sessionId: string,
    partNumber: number,
    request: PresignPartRequest,
    signal?: AbortSignal,
  ): Promise<PresignPartResponse>;
  completeSession(
    sessionId: string,
    request: CompleteUploadRequest,
    signal?: AbortSignal,
  ): Promise<CompleteUploadResponse>;
  abortSession(sessionId: string, signal?: AbortSignal): Promise<void>;
}

export interface UploadWorkspaceDependencies {
  createApiClient: (getToken: () => string | null) => UploadApiPort;
  startHashJob: (file: File, options: StartHashJobOptions) => HashJob;
  uploadPart: (options: UploadPartWithXhrOptions) => XhrUploadHandle;
  idempotencyKeyFactory: () => string;
  createScheduler?: () => PartUploadScheduler;
}

export interface UploadController {
  state: UploadMachineState;
  token: string | null;
  runtimeError: string | null;
  dispatch(action: UploadAction): boolean;
  saveToken(token: string): boolean;
  clearToken(): void;
}

interface ActiveTransfer {
  controller: AbortController;
  handle: XhrUploadHandle;
}

function errorDetails(error: unknown): { code: string; message: string } {
  if (
    error instanceof UploadApiError ||
    error instanceof UploadNetworkError ||
    error instanceof HashWorkerClientError ||
    error instanceof XhrUploadError
  ) {
    return { code: error.code, message: error.message };
  }
  if (error instanceof UploadApiProtocolError) {
    return { code: "protocol_error", message: error.message };
  }
  if (error instanceof UploadAuthenticationError) {
    return { code: "authentication_required", message: error.message };
  }
  if (error instanceof UploadPersistenceError) {
    return { code: "persistence_error", message: error.message };
  }
  if (error instanceof Error) {
    return { code: "unexpected_error", message: error.message };
  }
  return { code: "unexpected_error", message: "The upload operation failed unexpectedly." };
}

function transferKey(generation: number, partNumber: number, attempt: number): string {
  return `${generation}:${partNumber}:${attempt}`;
}

export function useUploadController(
  dependencies: UploadWorkspaceDependencies,
  storage: Storage,
): UploadController {
  const stores = useMemo(
    () => ({
      recovery: createUploadRecoveryStore(storage),
      token: createUploadTokenStore(storage),
    }),
    [storage],
  );
  const initialToken = useMemo(() => {
    try {
      return stores.token.load();
    } catch {
      return null;
    }
  }, [stores]);
  const [token, setToken] = useState<string | null>(initialToken);
  const [state, setState] = useState<UploadMachineState>(initialUploadState);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const tokenRef = useRef<string | null>(initialToken);
  const stateRef = useRef<UploadMachineState>(initialUploadState);
  const hashJobRef = useRef<HashJob | null>(null);
  const activeTransfersRef = useRef(new Map<string, ActiveTransfer>());
  const initializedRef = useRef(false);
  const schedulerRef = useRef<PartUploadScheduler | null>(null);
  const executeEffectsRef = useRef<(effects: readonly UploadEffect[]) => void>(() => undefined);

  if (schedulerRef.current === null) {
    schedulerRef.current = dependencies.createScheduler?.() ?? new PartUploadScheduler();
  }

  const api = useMemo(
    () => dependencies.createApiClient(() => tokenRef.current),
    [dependencies],
  );

  const dispatch = useCallback((action: UploadAction): boolean => {
    const transition = reduceUpload(stateRef.current, action);
    if (!transition.accepted) {
      return false;
    }
    stateRef.current = transition.state;
    setState(transition.state);
    executeEffectsRef.current(transition.effects);
    return true;
  }, []);

  const setBackgroundError = useCallback((error: unknown): void => {
    setRuntimeError(errorDetails(error).message);
  }, []);

  const runScheduledPart = useCallback(
    (task: Extract<UploadEffect, { type: "queue_parts" }>, part: (typeof task.parts)[number]) => {
      const scheduled: ScheduledPartTask = {
        partNumber: part.partNumber,
        attempt: part.attempt,
        generation: task.generation,
        run: async () => {
          if (
            !dispatch({
              type: "part_presign_started",
              generation: task.generation,
              partNumber: part.partNumber,
              attempt: part.attempt,
            })
          ) {
            return;
          }
          try {
            const presigned = await api.presignPart(task.sessionId, part.partNumber, {
              sizeBytes: part.sizeBytes,
              checksumSha256: part.checksumSha256,
            });
            if (
              !dispatch({
                type: "part_upload_started",
                generation: task.generation,
                partNumber: part.partNumber,
                attempt: part.attempt,
              })
            ) {
              return;
            }

            const controller = new AbortController();
            const key = transferKey(task.generation, part.partNumber, part.attempt);
            const handle = dependencies.uploadPart({
              url: presigned.url,
              headers: presigned.headers,
              body: task.file.slice(part.startByte, part.endByte),
              signal: controller.signal,
              onProgress: (uploadedBytes) => {
                dispatch({
                  type: "part_progress",
                  generation: task.generation,
                  partNumber: part.partNumber,
                  attempt: part.attempt,
                  uploadedBytes,
                });
              },
            });
            activeTransfersRef.current.set(key, { controller, handle });
            try {
              const result = await handle.result;
              dispatch({
                type: "part_uploaded",
                generation: task.generation,
                partNumber: part.partNumber,
                attempt: part.attempt,
                etag: result.etag,
              });
            } finally {
              activeTransfersRef.current.delete(key);
            }
          } catch (error) {
            const details = errorDetails(error);
            dispatch({
              type: "part_failed",
              generation: task.generation,
              partNumber: part.partNumber,
              attempt: part.attempt,
              code: details.code,
            });
          }
        },
      };
      schedulerRef.current?.enqueue(scheduled);
    },
    [api, dependencies, dispatch],
  );

  const executeEffect = useCallback(
    (effect: UploadEffect): void => {
      switch (effect.type) {
        case "hash_file": {
          hashJobRef.current?.cancel();
          const job = dependencies.startHashJob(effect.file, {
            partSizeBytes: effect.partSizeBytes,
            onProgress: (processedBytes, totalBytes) => {
              dispatch({
                type: "hash_progress",
                generation: effect.generation,
                processedBytes,
                totalBytes,
              });
            },
          });
          hashJobRef.current = job;
          void job.result.then(
            (result) => {
              if (hashJobRef.current === job) {
                hashJobRef.current = null;
              }
              dispatch({ type: "hash_succeeded", generation: effect.generation, result });
            },
            (error: unknown) => {
              if (hashJobRef.current === job) {
                hashJobRef.current = null;
              }
              const details = errorDetails(error);
              dispatch({
                type: "hash_failed",
                generation: effect.generation,
                code: details.code,
                message: details.message,
              });
            },
          );
          return;
        }
        case "create_session":
          void api.createSession(effect.request, effect.idempotencyKey).then(
            (session) => dispatch({ type: "session_created", generation: effect.generation, session }),
            (error: unknown) => {
              const details = errorDetails(error);
              dispatch({
                type: "session_create_failed",
                generation: effect.generation,
                code: details.code,
                message: details.message,
              });
            },
          );
          return;
        case "persist_session":
          try {
            stores.recovery.save(effect.session);
          } catch (error) {
            setBackgroundError(error);
          }
          return;
        case "clear_persistence":
          try {
            stores.recovery.clear();
          } catch (error) {
            setBackgroundError(error);
          }
          return;
        case "fetch_session":
          void api.getSession(effect.sessionId).then(
            (session) => dispatch({ type: "session_reconciled", generation: effect.generation, session }),
            (error: unknown) => {
              const details = errorDetails(error);
              dispatch({
                type: "session_reconcile_failed",
                generation: effect.generation,
                code: details.code,
                message: details.message,
              });
            },
          );
          return;
        case "queue_parts":
          for (const part of effect.parts) {
            try {
              runScheduledPart(effect, part);
            } catch (error) {
              setBackgroundError(error);
            }
          }
          return;
        case "pause_scheduler":
          schedulerRef.current?.pause(effect.clearQueued);
          return;
        case "resume_scheduler":
          schedulerRef.current?.resume();
          return;
        case "clear_scheduler":
          schedulerRef.current?.clearQueued();
          return;
        case "abort_hash":
          hashJobRef.current?.cancel();
          hashJobRef.current = null;
          return;
        case "abort_transfers":
          for (const transfer of activeTransfersRef.current.values()) {
            transfer.controller.abort();
            transfer.handle.abort();
          }
          return;
        case "complete_session":
          void api.completeSession(effect.sessionId, { parts: effect.parts }).then(
            (result) => dispatch({ type: "complete_succeeded", generation: effect.generation, result }),
            (error: unknown) => {
              const details = errorDetails(error);
              dispatch({
                type: "complete_failed",
                generation: effect.generation,
                code: details.code,
                message: details.message,
              });
            },
          );
          return;
        case "abort_session":
          void api.abortSession(effect.sessionId).catch(setBackgroundError);
      }
    },
    [api, dependencies, dispatch, runScheduledPart, setBackgroundError, stores],
  );

  executeEffectsRef.current = (effects) => {
    for (const effect of effects) {
      executeEffect(effect);
    }
  };

  useEffect(() => {
    if (initializedRef.current) {
      return;
    }
    initializedRef.current = true;
    try {
      const recovery = stores.recovery.load();
      if (recovery !== null) {
        dispatch({ type: "restore_session", session: recovery });
      }
    } catch (error) {
      setBackgroundError(error);
    }
  }, [dispatch, setBackgroundError, stores]);

  useEffect(
    () => {
      const activeTransfers = activeTransfersRef.current;
      schedulerRef.current?.resume();
      return () => {
        hashJobRef.current?.cancel();
        for (const transfer of activeTransfers.values()) {
          transfer.controller.abort();
          transfer.handle.abort();
        }
        schedulerRef.current?.pause(true);
      };
    },
    [],
  );

  const saveToken = useCallback(
    (nextToken: string): boolean => {
      try {
        stores.token.save(nextToken);
        tokenRef.current = nextToken;
        setToken(nextToken);
        setRuntimeError(null);
        return true;
      } catch (error) {
        setBackgroundError(error);
        return false;
      }
    },
    [setBackgroundError, stores],
  );

  const clearToken = useCallback(() => {
    try {
      stores.token.clear();
      tokenRef.current = null;
      setToken(null);
      setRuntimeError(null);
    } catch (error) {
      setBackgroundError(error);
    }
  }, [setBackgroundError, stores]);

  return { state, token, runtimeError, dispatch, saveToken, clearToken };
}
