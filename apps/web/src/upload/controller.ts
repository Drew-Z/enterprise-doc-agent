import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createApplicationCredentialStore } from "../auth/credentialStore";
import type { ApiCredential } from "../auth/transport";

import {
  UploadApiError,
  UploadApiProtocolError,
  UploadAuthenticationError,
  UploadNetworkError,
} from "./api/client";
import type {
  CompleteUploadRequest,
  CompleteUploadResponse,
  ContentUploadResponse,
  CreateUploadRequest,
  CreateUploadResponse,
  GetUploadResponse,
  PresignPartRequest,
  PresignPartResponse,
  UploadTransport,
} from "./api/schemas";
import { createContentIntentStore, type ContentUploadIntent } from "./contentIntent";
import { HashWorkerClientError, type HashJob, type StartHashJobOptions } from "./hashing/client";
import {
  createUploadRecoveryStore,
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
  uploadContent?(request: CreateUploadRequest, idempotencyKey: string, file: File, signal?: AbortSignal): Promise<ContentUploadResponse>;
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
    transport?: UploadTransport,
  ): Promise<PresignPartResponse>;
  completeSession(
    sessionId: string,
    request: CompleteUploadRequest,
    signal?: AbortSignal,
    transport?: UploadTransport,
  ): Promise<CompleteUploadResponse>;
  abortSession(sessionId: string, signal?: AbortSignal): Promise<void>;
}

export interface UploadWorkspaceDependencies {
  createApiClient: (getToken: () => ApiCredential | null) => UploadApiPort;
  startHashJob: (file: File, options: StartHashJobOptions) => HashJob;
  uploadPart: (options: UploadPartWithXhrOptions) => XhrUploadHandle;
  idempotencyKeyFactory: () => string;
  createScheduler?: () => PartUploadScheduler;
}

export interface UploadController {
  state: UploadMachineState;
  token: ApiCredential | null;
  runtimeError: string | null;
  dispatch: (action: UploadAction) => boolean;
  saveToken(token: string): boolean;
  clearToken(): void;
}

interface ActiveTransfer {
  controller: AbortController;
  handle: XhrUploadHandle;
}

function errorDetails(error: unknown): { code: string; message: string; requestId: string | null } {
  if (
    error instanceof UploadApiError ||
    error instanceof UploadNetworkError ||
    error instanceof HashWorkerClientError ||
    error instanceof XhrUploadError
  ) {
    return { code: error.code, message: error.message, requestId: error instanceof UploadApiError ? error.requestId : null };
  }
  if (error instanceof UploadApiProtocolError) {
    return { code: "protocol_error", message: error.message, requestId: null };
  }
  if (error instanceof UploadAuthenticationError) {
    return { code: "authentication_required", message: error.message, requestId: null };
  }
  if (error instanceof UploadPersistenceError) {
    return { code: "persistence_error", message: error.message, requestId: null };
  }
  if (error instanceof Error) {
    return { code: "unexpected_error", message: error.message, requestId: null };
  }
  return { code: "unexpected_error", message: "The upload operation failed unexpectedly.", requestId: null };
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
      content: createContentIntentStore(storage),
      token: createApplicationCredentialStore(storage),
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
  const [token, setToken] = useState<ApiCredential | null>(initialToken);
  const [state, setState] = useState<UploadMachineState>(initialUploadState);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const tokenRef = useRef<ApiCredential | null>(initialToken);
  const mountedRef = useRef(true);
  const stateRef = useRef<UploadMachineState>(initialUploadState);
  const hashJobRef = useRef<HashJob | null>(null);
  const activeTransfersRef = useRef(new Map<string, ActiveTransfer>());
  const initializedRef = useRef(false);
  const recoveryBlockedRef = useRef(false);
  const contentRequestsRef = useRef(new Set<AbortController>());
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
    if (!mountedRef.current || (typeof tokenRef.current === "object" && tokenRef.current?.signal.aborted)) return false;
    if (recoveryBlockedRef.current && action.type === "select_file") return false;
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
    const details = errorDetails(error);
    setRuntimeError(details.requestId ? `${details.message} (Request ID: ${details.requestId})` : details.message);
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
            }, undefined, task.transport);
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
              const result = await handle.result.catch(async (error: unknown) => {
                if (task.transport !== "single_put" || !(error instanceof XhrUploadError) || error.status !== 412) {
                  throw error;
                }
                const observed = await api.getSession(task.sessionId, controller.signal);
                const uploaded = observed.uploadedParts[0];
                if (observed.sessionId !== task.sessionId || observed.transport !== "single_put" ||
                    observed.status !== "active" || observed.sizeBytes !== task.file.size ||
                    observed.uploadedParts.length !== 1 || uploaded?.partNumber !== 1 ||
                    uploaded.sizeBytes !== part.sizeBytes || uploaded.checksumSha256 !== part.checksumSha256 ||
                    !uploaded.etag) {
                  throw new UploadApiProtocolError("Existing object does not match the selected upload.");
                }
                return { etag: uploaded.etag };
              });
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
              requestId: details.requestId,
              });
            },
          );
          return;
        }
        case "create_session": {
          const file = stateRef.current.file;
          if (api.uploadContent && effect.request.transport === "single_put" && file !== null) {
            const intent: ContentUploadIntent = { version: 1, idempotencyKey: effect.idempotencyKey,
              request: { ...effect.request, transport: "single_put" } };
            try { stores.content.save(intent); }
            catch (error) {
              const details = errorDetails(error);
              dispatch({ type: "session_create_failed", generation: effect.generation, ...details });
              return;
            }
            if (!dispatch({ type: "content_started", generation: effect.generation, intent })) return;
            const credential = tokenRef.current;
            const controller = new AbortController();
            contentRequestsRef.current.add(controller);
            const isCurrentCredential = () => mountedRef.current && tokenRef.current === credential && !controller.signal.aborted;
            void api.uploadContent(effect.request, effect.idempotencyKey, file, controller.signal).then(
              (result) => {
                if (!isCurrentCredential()) return;
                if (result.completion !== null) dispatch({ type: "content_completed", generation: effect.generation, session: result.session, result: result.completion });
                else dispatch({ type: "session_created", generation: effect.generation, session: result.session });
              },
              async (error: unknown) => {
                if (!isCurrentCredential()) return;
                if (error instanceof UploadApiError &&
                    ((error.status === 404 && ["upload_content_unsupported", "http_not_found"].includes(error.code)) ||
                     (error.status === 405 && error.code === "http_method_not_allowed"))) {
                  try {
                    const session = await api.createSession(effect.request, effect.idempotencyKey, controller.signal);
                    if (isCurrentCredential()) dispatch({ type: "session_created", generation: effect.generation, session });
                    return;
                  } catch (fallbackError) { error = fallbackError; }
                }
                if (isCurrentCredential()) {
                  const details = errorDetails(error);
                  dispatch({ type: "session_create_failed", generation: effect.generation, ...details });
                }
              },
            ).finally(() => contentRequestsRef.current.delete(controller));
            return;
          }
          void api.createSession(effect.request, effect.idempotencyKey).then(
            (session) => dispatch({ type: "session_created", generation: effect.generation, session }),
            (error: unknown) => {
              const details = errorDetails(error);
              dispatch({
                type: "session_create_failed",
                generation: effect.generation,
                code: details.code,
                message: details.message,
                requestId: details.requestId,
              });
            },
          );
          return;
        }
        case "cancel_content": {
          const controller = new AbortController();
          const credential = tokenRef.current;
          contentRequestsRef.current.add(controller);
          void api.abortSession(effect.sessionId, controller.signal).then(
            () => {
              if (tokenRef.current === credential && !controller.signal.aborted) dispatch({ type: "content_canceled", generation: effect.generation });
            },
            () => {
              if (mountedRef.current && tokenRef.current === credential && !controller.signal.aborted) {
                executeEffectsRef.current([{ type: "recover_content", generation: effect.generation, intent: effect.intent }]);
              }
            },
          ).finally(() => contentRequestsRef.current.delete(controller));
          return;
        }
        case "recover_content": {
          const controller = new AbortController();
          const credential = tokenRef.current;
          contentRequestsRef.current.add(controller);
          void api.createSession(effect.intent.request, effect.intent.idempotencyKey, controller.signal).then(
            (session) => {
              if (tokenRef.current === credential && !controller.signal.aborted) dispatch({ type: "content_recovered", generation: effect.generation, session });
            },
            () => {
              if (tokenRef.current === credential && !controller.signal.aborted) dispatch({ type: "content_recovery_failed", generation: effect.generation });
            },
          ).finally(() => contentRequestsRef.current.delete(controller));
          return;
        }
        case "persist_session":
          try {
            stores.recovery.save(effect.session);
            stores.content.clear();
          } catch (error) {
            setBackgroundError(error);
          }
          return;
        case "clear_persistence":
          try {
            stores.recovery.clear();
            stores.content.clear();
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
                requestId: details.requestId,
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
        case "complete_session": {
          const completion = effect.transport === "single_put"
            ? api.completeSession(effect.sessionId, { parts: effect.parts }, undefined, effect.transport)
            : api.completeSession(effect.sessionId, { parts: effect.parts });
          void completion.then(
            (result) => dispatch({ type: "complete_succeeded", generation: effect.generation, result }),
            (error: unknown) => {
              const details = errorDetails(error);
              dispatch({
                type: "complete_failed",
                generation: effect.generation,
                code: details.code,
                message: details.message,
                requestId: details.requestId,
              });
            },
          );
          return;
        }
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
      const intent = stores.content.load();
      if (intent !== null) {
        dispatch({ type: "restore_content_intent", intent });
        return;
      }
      const recovery = stores.recovery.load();
      if (recovery !== null) {
        dispatch({ type: "restore_session", session: recovery });
      }
    } catch (error) {
      recoveryBlockedRef.current = true;
      setBackgroundError(error);
    }
  }, [dispatch, setBackgroundError, stores]);

  useEffect(
    () => {
      const activeTransfers = activeTransfersRef.current;
      const contentRequests = contentRequestsRef.current;
      mountedRef.current = true;
      schedulerRef.current?.resume();
      const pending = stateRef.current;
      if (pending.contentIntent !== null && pending.reconciling &&
          [...contentRequests].every(controller => controller.signal.aborted)) {
        executeEffectsRef.current([{ type: "recover_content", generation: pending.generation, intent: pending.contentIntent }]);
      }
      const cancelWork = () => {
        hashJobRef.current?.cancel();
        for (const controller of contentRequests) controller.abort();
        for (const transfer of activeTransfers.values()) {
          transfer.controller.abort();
          transfer.handle.abort();
        }
        schedulerRef.current?.pause(true);
      };
      const signal = typeof tokenRef.current === "object" ? tokenRef.current?.signal : undefined;
      signal?.addEventListener("abort", cancelWork, { once: true });
      return () => {
        mountedRef.current = false;
        signal?.removeEventListener("abort", cancelWork);
        cancelWork();
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
