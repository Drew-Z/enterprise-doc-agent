import { useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  CircleX,
  FileText,
  FolderOpen,
  KeyRound,
  LoaderCircle,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Trash2,
  Upload,
} from "lucide-react";

import { UploadApiClient } from "./api/client";
import { startHashJob } from "./hashing/client";
import { aggregateUploadProgress } from "./state/progress";
import type { UploadMachineState, UploadPhase } from "./state/types";
import {
  useUploadController,
  type UploadWorkspaceDependencies,
} from "./controller";
import { uploadPartWithXhr } from "./transfer/xhrUploadPart";
import { useLocale, useT } from "../i18n";
import { isBrowserAuthentication } from "../auth/transport";
import { formatApiError } from "../api/errorDisplay";
import "./workspace.css";

const localObjectStoreOrigins = (
  import.meta.env.VITE_OBJECT_STORE_ORIGINS ?? "http://127.0.0.1:9000"
)
  .split(",")
  .map((origin) => origin.trim())
  .filter((origin) => origin !== "");

const defaultUploadWorkspaceDependencies: UploadWorkspaceDependencies = {
  createApiClient: (getToken) =>
    new UploadApiClient({
      baseUrl: import.meta.env.VITE_API_BASE_URL,
      getToken,
      allowedObjectStoreOrigins: localObjectStoreOrigins,
    }),
  startHashJob,
  uploadPart: uploadPartWithXhr,
  idempotencyKeyFactory: () => crypto.randomUUID(),
};

export interface UploadWorkspaceProps {
  dependencies?: UploadWorkspaceDependencies;
  storage?: Storage;
  onTokenChange?: () => void;
  onCompleted?: () => void;
  canUpload?: boolean;
}

const phaseLabelKeys: Record<UploadPhase, Parameters<ReturnType<typeof useT>>[0]> = {
  idle: "upload.phase.idle",
  awaiting_file: "upload.phase.awaitingFile",
  hashing: "upload.phase.hashing",
  creating: "upload.phase.creating",
  uploading: "upload.phase.uploading",
  paused: "upload.phase.paused",
  completing: "upload.phase.completing",
  completed: "upload.phase.completed",
  failed: "upload.phase.failed",
  canceled: "upload.phase.canceled",
};

function mediaTypeForFilename(filename: string): string | null {
  const extension = filename.toLowerCase().split(".").pop();
  switch (extension) {
    case "txt":
      return "text/plain";
    case "pdf":
      return "application/pdf";
    case "docx":
      return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    default:
      return null;
  }
}

function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let amount = value;
  let unit = "B";
  for (const nextUnit of units) {
    amount /= 1024;
    unit = nextUnit;
    if (amount < 1024) {
      break;
    }
  }
  return `${amount >= 10 ? amount.toFixed(1) : amount.toFixed(2)} ${unit}`;
}

function progressForState(state: UploadMachineState, t: ReturnType<typeof useT>): { percent: number; label: string } {
  if (state.phase === "hashing" && state.file !== null) {
    const percent = (state.hashProcessedBytes / state.file.size) * 100;
    return {
      percent,
      label: t("upload.progress.of", { uploaded: formatBytes(state.hashProcessedBytes), total: formatBytes(state.file.size) }),
    };
  }
  const progress = aggregateUploadProgress(state.parts);
  return {
    percent: state.phase === "completed" ? 100 : progress.percent,
    label:
      progress.totalBytes === 0
        ? t("upload.progress.none")
        : t("upload.progress.of", { uploaded: formatBytes(progress.uploadedBytes), total: formatBytes(progress.totalBytes) }),
  };
}

function partStatusLabel(status: string, t: ReturnType<typeof useT>): string {
  if (status === "pending") return t("upload.status.pending");
  if (status === "uploading") return t("upload.status.uploading");
  if (status === "uploaded") return t("upload.status.uploaded");
  if (status === "failed") return t("upload.status.failed");
  if (status === "canceled") return t("upload.status.canceled");
  return status;
}

function canCancel(state: UploadMachineState): boolean {
  return (
    ["awaiting_file", "hashing", "creating", "uploading", "paused", "failed"].includes(state.phase) &&
    !state.reconciling && state.failure?.code !== "session_completing" &&
    !(state.phase === "failed" && state.failure?.stage === "complete")
  );
}

interface QueuedFile {
  id: string;
  file: File | null;
  label: string;
  mediaType: string;
  status: "queued" | "active" | "completed" | "canceled";
}

function canClear(state: UploadMachineState): boolean {
  return (
    ["completed", "canceled", "failed"].includes(state.phase) &&
    !(state.phase === "failed" && state.session !== null)
  );
}

export function UploadWorkspace({
  dependencies = defaultUploadWorkspaceDependencies,
  storage = sessionStorage,
  onTokenChange,
  onCompleted,
  canUpload = true,
}: UploadWorkspaceProps) {
  const t = useT();
  const locale = useLocale();
  const browserMode = isBrowserAuthentication();
  const controller = useUploadController(dependencies, storage);
  const { dispatch, token } = controller;
  const [tokenDraft, setTokenDraft] = useState(typeof controller.token === "string" ? controller.token : "");
  const [inputError, setInputError] = useState<string | null>(null);
  const [queue, setQueue] = useState<QueuedFile[]>([]);
  const activeFile = useRef<string | null>(null);
  const notifiedCompletion = useRef<string | null>(null);
  const progress = useMemo(() => progressForState(controller.state, t), [controller.state, t]);
  const state = controller.state;
  const choosingOriginal = state.phase === "awaiting_file" || (state.phase === "failed" && state.failure?.stage === "file_identity");
  const copy = locale === "zh" ? {
    folder: "选择文件夹", queue: "上传队列", hint: "可多选文件或选择文件夹。文件依次上传；失败后可重试或取消当前文件，再继续队列。刷新页面后，未上传的文件需要重新选择。",
    invalid: "已跳过不支持的类型或空文件：", limit: "每批最多 100 个文件，请分批选择。", queued: "等待上传", completed: "已上传", canceled: "已取消", remove: "移出队列", clear: "清除已结束记录", checking: "正在核对上传结果…",
  } : {
    folder: "Choose folder", queue: "Upload queue", hint: "Select multiple files or a folder. Files upload one at a time; retry or cancel a failed file to continue. After reload, select pending files again.",
    invalid: "Skipped unsupported or empty files: ", limit: "Select up to 100 files per batch.", queued: "Queued", completed: "Done", canceled: "Canceled", remove: "Remove from queue", clear: "Clear finished entries", checking: "Checking upload result…",
  };

  useEffect(() => {
    setTokenDraft(typeof controller.token === "string" ? controller.token : "");
  }, [controller.token]);

  useEffect(() => {
    if (state.phase === "completed" && state.session !== null && notifiedCompletion.current !== state.session.sessionId) {
      notifiedCompletion.current = state.session.sessionId;
      onCompleted?.();
    }
  }, [onCompleted, state.phase, state.session]);

  useEffect(() => {
    if (!["idle", "completed", "canceled"].includes(state.phase)) return;
    if (activeFile.current !== null) {
      const finished = activeFile.current;
      activeFile.current = null;
      setQueue(current => current.map(item => item.id === finished ? { ...item, file: null, status: state.phase === "completed" ? "completed" : "canceled" } : item));
      return;
    }
    if (!canUpload || token === null) return;
    const next = queue.find(item => item.status === "queued");
    if (!next?.file) return;
    activeFile.current = next.id;
    if (dispatch({ type: "select_file", file: next.file, mediaType: next.mediaType, idempotencyKey: next.id })) {
      setQueue(current => current.map(item => item.id === next.id ? { ...item, status: "active" } : item));
    } else activeFile.current = null;
  }, [canUpload, dispatch, token, queue, state.phase]);

  const handleFiles = (files: File[]): void => {
    if (files.length === 0 || !canUpload) {
      return;
    }
    setInputError(null);
    if (choosingOriginal) {
      controller.dispatch({ type: "reselect_file", file: files[0] });
      return;
    }
    if (files.length + queue.length > 100) {
      setInputError(copy.limit);
      return;
    }
    const added: QueuedFile[] = [];
    const skipped: string[] = [];
    for (const file of files) {
      const mediaType = mediaTypeForFilename(file.name);
      const label = file.webkitRelativePath || file.name;
      if (mediaType === null || file.size === 0) { skipped.push(label); continue; }
      added.push({ id: dependencies.idempotencyKeyFactory(), file, label, mediaType, status: "queued" });
    }
    if (skipped.length > 0) setInputError(copy.invalid + skipped.slice(0, 10).join(", ") + (skipped.length > 10 ? "…" : ""));
    setQueue(current => [...current, ...added]);
  };

  const tokenConnected = controller.token !== null;
  const fileInputDisabled =
    !canUpload || !tokenConnected || (choosingOriginal && state.reconciling);
  const alertMessage = inputError ?? (state.failure !== null
    ? formatApiError(state.failure, t("upload.requestFailed"), t("common.requestId"))
    : controller.runtimeError);

  return (
    <section className="upload-workspace" aria-labelledby="upload-title">
      <div className="upload-heading">
        <div>
          <p className="eyebrow">{t("upload.eyebrow")}</p>
          <h1 id="upload-title">{t("upload.title")}</h1>
        </div>
        <span className={`token-state ${tokenConnected ? "connected" : "disconnected"}`}>
          <KeyRound aria-hidden="true" />
          {browserMode ? (locale === "zh" ? "已登录" : "Signed in") : tokenConnected ? t("upload.tokenSaved") : t("upload.tokenRequired")}
        </span>
      </div>

      {!browserMode && <div className="auth-strip">
        <label htmlFor="local-api-token">{t("upload.localToken")}</label>
        <div className="auth-controls">
          <input
            id="local-api-token"
            type="password"
            autoComplete="off"
            value={tokenDraft}
            onChange={(event) => setTokenDraft(event.target.value)}
            placeholder="JWT"
          />
          <button
            className="command-button"
            type="button"
            onClick={() => {
              controller.saveToken(tokenDraft);
              onTokenChange?.();
            }}
          >
            <Check aria-hidden="true" />
            {t("upload.saveToken")}
          </button>
          <button
            className="icon-button"
            type="button"
            aria-label={t("upload.clearToken")}
            title={t("upload.clearToken")}
            disabled={!tokenConnected}
            onClick={() => {
              controller.clearToken();
              onTokenChange?.();
            }}
          >
            <Trash2 aria-hidden="true" />
          </button>
        </div>
        <small className="auth-boundary">{t("upload.localTokenDetail")}</small>
      </div>}

      <div className="upload-command-bar">
        <div className="file-picker">
          <FileText aria-hidden="true" />
          <label htmlFor="upload-file">
            {choosingOriginal ? t("upload.chooseOriginal") : t("upload.chooseDocument")}
          </label>
          <input
            id="upload-file"
            type="file"
            multiple={!choosingOriginal}
            accept=".txt,.pdf,.docx,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            disabled={fileInputDisabled}
            onChange={(event) => { handleFiles(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = ""; }}
          />
        </div>

        {!choosingOriginal && <div className="file-picker folder-picker">
          <FolderOpen aria-hidden="true" />
          <label htmlFor="upload-folder">{copy.folder}</label>
          <input id="upload-folder" type="file" multiple {...{ webkitdirectory: "" }} disabled={fileInputDisabled}
            onChange={event => { handleFiles(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = ""; }} />
        </div>}

        <div className="upload-actions" aria-label={t("upload.actions")}>
          {state.phase === "uploading" && !state.reconciling && (
            <button
              className="icon-button"
              type="button"
              aria-label={t("upload.pause")}
              title={t("upload.pause")}
              onClick={() => controller.dispatch({ type: "pause" })}
            >
              <Pause aria-hidden="true" />
            </button>
          )}
          {state.phase === "paused" && (
            <button
              className="icon-button primary-icon"
              type="button"
              aria-label={t("upload.resume")}
              title={t("upload.resume")}
              onClick={() => controller.dispatch({ type: "resume" })}
            >
              <Play aria-hidden="true" />
            </button>
          )}
          {state.phase === "failed" && state.failure?.retryable === true && !state.reconciling && (
            <button
              className="icon-button"
              type="button"
              aria-label={t("upload.retry")}
              title={t("upload.retry")}
              onClick={() => controller.dispatch({ type: "retry" })}
            >
              <RefreshCw aria-hidden="true" />
            </button>
          )}
          {canCancel(state) && (
            <button
              className="icon-button danger-icon"
              type="button"
              aria-label={t("upload.cancel")}
              title={t("upload.cancel")}
              onClick={() => controller.dispatch({ type: "cancel" })}
            >
              <CircleX aria-hidden="true" />
            </button>
          )}
          {canClear(state) && (
            <button
              className="icon-button"
              type="button"
              aria-label={t("upload.startAnother")}
              title={t("upload.startAnother")}
              onClick={() => controller.dispatch({ type: "clear" })}
            >
              <RotateCcw aria-hidden="true" />
            </button>
          )}
        </div>
      </div>

      <p className="upload-queue-hint">{copy.hint}</p>
      {queue.length > 0 && <section className="upload-queue" aria-label={copy.queue}>
        <div className="section-heading"><h2>{copy.queue} ({queue.length})</h2>
          <button type="button" className="command-button" disabled={!queue.some(item => ["completed", "canceled"].includes(item.status))}
            onClick={() => setQueue(current => current.filter(item => ["queued", "active"].includes(item.status)))}>{copy.clear}</button>
        </div>
        <ul>{queue.map(item => <li key={item.id}>
          <span>{item.label}</span>
          <small>{item.status === "active" ? `${t(phaseLabelKeys[state.phase])} · ${Math.round(progress.percent)}%` : copy[item.status]}</small>
          {item.status === "queued" && <button type="button" className="icon-button" aria-label={`${copy.remove}: ${item.label}`}
            onClick={() => setQueue(current => current.filter(entry => entry.id !== item.id))}><Trash2 aria-hidden="true" /></button>}
        </li>)}</ul>
      </section>}

      <div className="upload-status" aria-live="polite">
        <div className="status-line">
          <div className="status-icon" aria-hidden="true">
            {state.phase === "completed" ? (
              <Check />
            ) : ["hashing", "creating", "uploading", "completing"].includes(state.phase) ? (
              <LoaderCircle className="spin" />
            ) : (
              <Upload />
            )}
          </div>
          <div>
            <h2>{state.reconciling ? copy.checking : t(phaseLabelKeys[state.phase])}</h2>
            <p>
              {state.fileIdentity?.filename ?? state.file?.name ?? t("upload.noDocument")}
              {(state.fileIdentity?.sizeBytes ?? state.file?.size) !== undefined
                ? ` · ${formatBytes(state.fileIdentity?.sizeBytes ?? state.file?.size ?? 0)}`
                : ""}
            </p>
          </div>
          <strong>{Math.round(progress.percent)}%</strong>
        </div>
        <div className="progress-track" aria-label={t("upload.progress")}>
          <span style={{ width: `${Math.max(0, Math.min(progress.percent, 100))}%` }} />
        </div>
        <span className="progress-detail">{progress.label}</span>
      </div>

      {alertMessage !== null && (
        <div className="upload-alert" role="alert">
          <CircleX aria-hidden="true" />
          <span>{alertMessage}</span>
        </div>
      )}

      {!browserMode && state.completion !== null && (
        <dl className="completion-result">
          <div>
            <dt>{t("upload.documentId")}</dt>
            <dd>{state.completion.documentId}</dd>
          </div>
          <div>
            <dt>{t("upload.versionId")}</dt>
            <dd>{state.completion.versionId}</dd>
          </div>
        </dl>
      )}

      {state.parts.length > 0 && (
        <div className="part-section" aria-labelledby="parts-title">
          <div className="section-heading">
            <h2 id="parts-title">{t("upload.parts")}</h2>
            <span>{t("upload.total", { value: String(state.parts.length) })}</span>
          </div>
          <div className="part-list">
            {state.parts.map((part) => (
              <article className="part-card" key={part.partNumber}>
                <div>
                  <strong>{t("upload.part", { value: String(part.partNumber) })}</strong>
                  <span>{formatBytes(part.sizeBytes)}</span>
                </div>
                <div className="part-progress">
                  <span
                    style={{
                      width: `${part.sizeBytes === 0 ? 0 : (part.uploadedBytes / part.sizeBytes) * 100}%`,
                    }}
                  />
                </div>
                <span className={`part-state ${part.status}`}>{partStatusLabel(part.status, t)}</span>
                {part.status === "failed" && (
                  <button
                    className="icon-button compact-icon"
                    type="button"
                    aria-label={t("upload.retryPart", { value: String(part.partNumber) })}
                    title={t("upload.retryPart", { value: String(part.partNumber) })}
                    onClick={() => controller.dispatch({ type: "retry_part", partNumber: part.partNumber })}
                  >
                    <RefreshCw aria-hidden="true" />
                  </button>
                )}
              </article>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
