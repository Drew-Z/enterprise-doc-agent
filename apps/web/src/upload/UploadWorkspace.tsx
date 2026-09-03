import { useEffect, useMemo, useState } from "react";
import {
  Check,
  CircleX,
  FileText,
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
import { useT } from "../i18n";
import { formatApiError } from "../api/errorDisplay";

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
    !(state.phase === "failed" && state.failure?.stage === "complete")
  );
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
  const controller = useUploadController(dependencies, storage);
  const [tokenDraft, setTokenDraft] = useState(controller.token ?? "");
  const [inputError, setInputError] = useState<string | null>(null);
  const progress = useMemo(() => progressForState(controller.state, t), [controller.state, t]);
  const state = controller.state;

  useEffect(() => {
    setTokenDraft(controller.token ?? "");
  }, [controller.token]);

  useEffect(() => {
    if (state.phase === "completed") onCompleted?.();
  }, [onCompleted, state.phase]);

  const handleFile = (file: File | undefined): void => {
    if (file === undefined || !canUpload) {
      return;
    }
    setInputError(null);
    if (state.phase === "awaiting_file") {
      controller.dispatch({ type: "reselect_file", file });
      return;
    }
    const mediaType = mediaTypeForFilename(file.name);
    if (mediaType === null) {
      setInputError(t("upload.error.fileType"));
      return;
    }
    controller.dispatch({
      type: "select_file",
      file,
      mediaType,
      idempotencyKey: dependencies.idempotencyKeyFactory(),
    });
  };

  const tokenConnected = controller.token !== null;
  const fileInputDisabled =
    !canUpload || !tokenConnected || !["idle", "awaiting_file", "completed", "canceled"].includes(state.phase);
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
          {tokenConnected ? t("upload.tokenSaved") : t("upload.tokenRequired")}
        </span>
      </div>

      <div className="auth-strip">
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
      </div>

      <div className="upload-command-bar">
        <div className="file-picker">
          <FileText aria-hidden="true" />
          <label htmlFor="upload-file">
            {state.phase === "awaiting_file" ? t("upload.chooseOriginal") : t("upload.chooseDocument")}
          </label>
          <input
            id="upload-file"
            type="file"
            accept=".txt,.pdf,.docx,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            disabled={fileInputDisabled}
            onChange={(event) => handleFile(event.currentTarget.files?.[0])}
          />
        </div>

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
          {state.phase === "failed" && state.failure?.retryable === true && (
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
            <h2>{t(phaseLabelKeys[state.phase])}</h2>
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

      {state.completion !== null && (
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
