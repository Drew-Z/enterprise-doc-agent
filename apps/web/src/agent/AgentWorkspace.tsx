import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Ban,
  Check,
  CircleAlert,
  CircleCheck,
  CircleX,
  Download,
  FileCheck2,
  FileText,
  Files,
  LoaderCircle,
  Play,
  RefreshCw,
  RotateCw,
  ShieldCheck,
  Square,
  X,
} from "lucide-react";

import {
  AgentApiClient,
  AgentApiError,
  AgentApiProtocolError,
  AgentAuthenticationError,
  AgentNetworkError,
  type AgentApiClientProtocol,
} from "./api/client";
import {
  agentRunTaskTypeSchema,
  type AgentRunStatus,
  type AgentRunStatusResponse,
  type AgentRunTaskType,
  type AgentArtifactPreviewResponse,
  type ApprovalRequestResponse,
  type CreateAgentRunRequest,
  type ReadyDocumentVersion,
} from "./api/schemas";
import { createUploadTokenStore } from "../upload/persistence";
import { type MessageKey, useLocale, useT } from "../i18n";
import { formatApiError } from "../api/errorDisplay";
import { createAgentRunRecoveryStore, type AgentRunRecoveryStore } from "./persistence";
import {
  abortableDelay,
  AgentSseProtocolError,
  eventResponseToTimeline,
  isTerminalAgentEvent,
  readAgentEventStream,
  type AgentTimelineEvent,
} from "./sse";

const defaultDependencies: AgentWorkspaceDependencies = {
  createApiClient: (getToken) =>
    new AgentApiClient({
      baseUrl: import.meta.env.VITE_API_BASE_URL,
      getToken,
    }),
  idempotencyKeyFactory: () => crypto.randomUUID(),
  openExternal: (url) => {
    window.open(url, "_blank", "noopener,noreferrer");
  },
};

const taskLabelKeys: Record<AgentRunTaskType, MessageKey> = {
  question_answer: "agent.task.question",
  summary: "agent.task.summary",
  structured_extraction: "agent.task.extraction",
};

const statusLabelKeys: Record<AgentRunStatus, MessageKey> = {
  pending: "agent.status.pending",
  running: "agent.status.running",
  waiting_approval: "agent.status.waitingApproval",
  succeeded: "agent.status.succeeded",
  refused: "agent.status.refused",
  failed: "agent.status.failed",
  cancelled: "agent.status.cancelled",
  rejected: "agent.status.rejected",
  expired: "agent.status.expired",
};

const streamLabelKeys: Record<"idle" | "loading" | "connected" | "reconnecting" | "closed", MessageKey> = {
  idle: "agent.stream.idle",
  loading: "agent.stream.loading",
  connected: "agent.stream.connected",
  reconnecting: "agent.stream.reconnecting",
  closed: "agent.stream.closed",
};

const approvalStatusLabelKeys: Record<ApprovalRequestResponse["status"], MessageKey> = {
  pending: "agent.approval.status.pending",
  approved: "agent.approval.status.approved",
  rejected: "agent.approval.status.rejected",
  expired: "agent.approval.status.expired",
  revoked: "agent.approval.status.revoked",
  consumed: "agent.approval.status.consumed",
};

const artifactStatusLabelKeys: Record<"writing" | "draft_ready" | "published" | "failed" | "revoked", MessageKey> = {
  writing: "agent.artifact.status.writing",
  draft_ready: "agent.artifact.status.draftReady",
  published: "agent.artifact.status.published",
  failed: "agent.artifact.status.failed",
  revoked: "agent.artifact.status.revoked",
};

const terminalStatuses = new Set<AgentRunStatus>([
  "succeeded",
  "refused",
  "failed",
  "cancelled",
  "rejected",
  "expired",
]);

const approvalStatuses = new Set<ApprovalRequestResponse["status"]>([
  "pending",
  "approved",
  "rejected",
  "expired",
  "revoked",
  "consumed",
]);

export interface AgentWorkspaceDependencies {
  createApiClient: (getToken: () => string | null) => AgentApiClientProtocol;
  idempotencyKeyFactory: () => string;
  openExternal: (url: string) => void;
}

export interface AgentWorkspaceProps {
  dependencies?: AgentWorkspaceDependencies;
  tokenStorage?: Storage;
  recoveryStorage?: Storage;
  initialRunId?: string;
  readOnly?: boolean;
  openDocuments?: () => void;
  canCreateRuns?: boolean;
  canDecideApproval?: boolean;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  const units = ["KiB", "MiB", "GiB"];
  let amount = value;
  let unit = "B";
  for (const next of units) {
    amount /= 1024;
    unit = next;
    if (amount < 1024) break;
  }
  return `${amount >= 10 ? amount.toFixed(1) : amount.toFixed(2)} ${unit}`;
}

function formatDate(value: string, locale: "en" | "zh"): string {
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function shortId(value: string): string {
  return `${value.slice(0, 8)}...${value.slice(-6)}`;
}

function formatCount(value: number | null, t: ReturnType<typeof useT>): string {
  return value === null ? t("agent.notReported") : value.toLocaleString();
}

function formatModel(status: AgentRunStatusResponse): string {
  const version = status.modelVersion === null ? null : `v${status.modelVersion}`;
  const revision = status.modelRevision === null ? null : status.modelRevision;
  return [status.modelProvider, status.modelName, version, revision].filter((part): part is string => part !== null).join(" · ");
}

function citationLocation(
  citation: AgentArtifactPreviewResponse["citations"][number],
  t: ReturnType<typeof useT>,
): string {
  const parts = [citation.sourceFilename ?? t("agent.authorizedSource")];
  if (citation.pageNumber !== null) parts.push(t("agent.page", { value: String(citation.pageNumber) }));
  if (citation.heading !== null) parts.push(citation.heading);
  return parts.join(" · ");
}

function describeError(error: unknown, t: ReturnType<typeof useT>): string {
  if (error instanceof AgentAuthenticationError) return t("agent.error.authentication");
  if (error instanceof AgentApiError) return formatApiError(error, t("agent.error.request"), t("common.requestId"));
  if (error instanceof AgentApiProtocolError) return error.message;
  if (error instanceof AgentNetworkError) return t("agent.error.network");
  if (error instanceof Error) return error.message;
  return t("agent.error.request");
}

function eventLabel(event: AgentTimelineEvent, t: ReturnType<typeof useT>): string {
  switch (event.eventType) {
    case "run.created":
      return t("agent.event.created");
    case "run.started":
      return t("agent.event.workerStarted");
    case "run.resumed":
      return t("agent.event.resumed");
    case "run.waiting_approval":
      return t("agent.event.approvalRequested");
    case "run.cancel_requested":
      return t("agent.event.cancelRequested");
    case "run.cancelled":
      return t("agent.event.cancelled");
    case "run.finished":
      return t("agent.event.finished", { value: String(event.payload.status) });
  }
}

function eventIcon(eventType: AgentTimelineEvent["eventType"]) {
  if (eventType === "run.finished") return CircleCheck;
  if (eventType === "run.cancelled") return CircleX;
  if (eventType === "run.waiting_approval") return ShieldCheck;
  return RotateCw;
}

function artifactKindLabel(kind: string, t: ReturnType<typeof useT>): string {
  return kind === "answer" ? t("agent.artifact.answer") : kind;
}

function artifactStatusLabel(status: string, t: ReturnType<typeof useT>): string {
  const key = artifactStatusLabelKeys[status as keyof typeof artifactStatusLabelKeys];
  return key === undefined ? status : t(key);
}

function riskLabel(value: string | null, t: ReturnType<typeof useT>): string {
  if (value === "low") return t("agent.risk.low");
  if (value === "medium") return t("agent.risk.medium");
  if (value === "high") return t("agent.risk.high");
  return value ?? t("agent.noRisk");
}

export function AgentWorkspace({
  dependencies = defaultDependencies,
  tokenStorage = sessionStorage,
  recoveryStorage = localStorage,
  initialRunId,
  readOnly = false,
  openDocuments,
  canCreateRuns = true,
  canDecideApproval = true,
}: AgentWorkspaceProps) {
  const t = useT();
  const locale = useLocale();
  const tokenStore = useMemo(() => createUploadTokenStore(tokenStorage), [tokenStorage]);
  const recoveryStore = useMemo<AgentRunRecoveryStore>(
    () => createAgentRunRecoveryStore(recoveryStorage),
    [recoveryStorage],
  );
  const client = useMemo(
    () => dependencies.createApiClient(() => tokenStore.load()),
    [dependencies, tokenStore],
  );
  const recovered = useMemo(() => recoveryStore.load(), [recoveryStore]);
  const [documents, setDocuments] = useState<ReadyDocumentVersion[]>([]);
  const [selectedDocumentId, setSelectedDocumentId] = useState("");
  const [taskType, setTaskType] = useState<AgentRunTaskType>("question_answer");
  const [inputText, setInputText] = useState("");
  const [publishRequested, setPublishRequested] = useState(false);
  const [extractionSchemaText, setExtractionSchemaText] = useState('{"type":"object"}');
  const [runId, setRunId] = useState<string | null>(initialRunId ?? recovered?.runId ?? null);
  const [lastSequence, setLastSequence] = useState(initialRunId === undefined ? recovered?.lastSequence ?? 0 : 0);
  const [events, setEvents] = useState<AgentTimelineEvent[]>([]);
  const [runStatus, setRunStatus] = useState<AgentRunStatus | null>(null);
  const [runDetails, setRunDetails] = useState<AgentRunStatusResponse | null>(null);
  const [approval, setApproval] = useState<ApprovalRequestResponse | null>(null);
  const [artifacts, setArtifacts] = useState<Awaited<ReturnType<AgentApiClientProtocol["listArtifacts"]>>>([]);
  const [artifactPreview, setArtifactPreview] = useState<AgentArtifactPreviewResponse | null>(null);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [streamState, setStreamState] = useState<"idle" | "loading" | "connected" | "reconnecting" | "closed">("idle");
  const [isCreating, setIsCreating] = useState(false);
  const [isCanceling, setIsCanceling] = useState(false);
  const [decisionState, setDecisionState] = useState<"idle" | "approving" | "rejecting">("idle");
  const [downloadingArtifactId, setDownloadingArtifactId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const cursorRef = useRef(lastSequence);
  const terminalRef = useRef(false);
  const [approvalRefresh, setApprovalRefresh] = useState(0);

  const applyEvent = useCallback(
    (event: AgentTimelineEvent) => {
      if (event.seq <= cursorRef.current) return;
      if (event.seq !== cursorRef.current + 1) {
        throw new Error(`Event sequence gap: expected ${cursorRef.current + 1}, received ${event.seq}.`);
      }
      cursorRef.current = event.seq;
      setLastSequence(event.seq);
      setEvents((current) => {
        if (current.some((item) => item.seq === event.seq)) return current;
        return [...current, event].sort((left, right) => left.seq - right.seq);
      });
      try {
        if (!readOnly && runId !== null) {
          recoveryStore.save({ version: 1, runId, lastSequence: event.seq });
        }
      } catch {
        // A storage failure must not interrupt a live run.
      }
      if (event.eventType === "run.waiting_approval") {
        setRunStatus("waiting_approval");
        setApproval((current) => current ?? null);
      }
      if (event.eventType === "run.started" || event.eventType === "run.resumed") {
        setRunStatus("running");
      }
      if (event.eventType === "run.finished") {
        const status = event.payload.status;
        if (typeof status === "string") {
          setRunStatus(status as AgentRunStatus);
        }
        terminalRef.current = true;
      }
      if (event.eventType === "run.cancelled") {
        setRunStatus("cancelled");
        terminalRef.current = true;
      }
    },
    [readOnly, recoveryStore, runId],
  );

  useEffect(() => {
    const controller = new AbortController();
    setErrorMessage(null);
    void (async () => {
      try {
        const result = await client.listReadyDocumentVersions(controller.signal);
        if (!controller.signal.aborted) setDocuments(result);
      } catch (error) {
        if (!controller.signal.aborted) setErrorMessage(describeError(error, t));
      }
    })();
    return () => controller.abort();
  }, [client, t]);

  useEffect(() => {
    if (runId === null) {
      setStreamState("idle");
      return;
    }
    const controller = new AbortController();
    const currentRunId = runId;
    let startingCursor = 0;
    try {
      const stored = recoveryStore.load();
      if (stored?.runId === currentRunId) startingCursor = stored.lastSequence;
    } catch {
      // A corrupt recovery record is cleared by the store and should not stop a run.
    }
    cursorRef.current = startingCursor;
    terminalRef.current = false;
    setEvents([]);
    setLastSequence(startingCursor);
    setRunDetails(null);
    setApproval(null);
    setArtifacts([]);
    setArtifactPreview(null);
    setStreamState("loading");
    void (async () => {
      let reconnectAttempt = 0;
      try {
        const status = await client.getRun(currentRunId, controller.signal);
        if (controller.signal.aborted) return;
        setRunDetails(status);
        setRunStatus(status.status);
        // The API caps one response at 500 events. Continue paging from the
        // current cursor so terminal runs with long histories are not truncated.
        let historyCursor = cursorRef.current;
        while (!controller.signal.aborted) {
          const history = await client.listEvents(currentRunId, historyCursor, controller.signal);
          for (const raw of history) {
            if (controller.signal.aborted) return;
            applyEvent(eventResponseToTimeline(raw));
          }
          const nextCursor = cursorRef.current;
          if (history.length < 500 || nextCursor === historyCursor) break;
          historyCursor = nextCursor;
        }
        if (terminalRef.current || terminalStatuses.has(status.status)) {
          setStreamState("closed");
          const finalStatus = terminalRef.current
            ? await client.getRun(currentRunId, controller.signal)
            : status;
          setRunDetails(finalStatus);
          setRunStatus(finalStatus.status);
          if (finalStatus.status === "succeeded") {
            const visibleArtifacts = await client.listArtifacts(currentRunId, controller.signal);
            setArtifacts(visibleArtifacts);
            const answerArtifact = visibleArtifacts.find((artifact) => artifact.kind === "answer");
            if (answerArtifact !== undefined) {
              setIsPreviewLoading(true);
              try {
                setArtifactPreview(await client.getArtifactPreview(answerArtifact.artifactId, controller.signal));
              } finally {
                setIsPreviewLoading(false);
              }
            }
          }
          return;
        }
        while (!controller.signal.aborted && !terminalRef.current) {
          try {
            setStreamState(reconnectAttempt === 0 ? "connected" : "reconnecting");
            const response = await client.openEventStream(currentRunId, cursorRef.current, controller.signal);
            reconnectAttempt = 0;
            for await (const event of readAgentEventStream(response, cursorRef.current)) {
              if (controller.signal.aborted) return;
              applyEvent(event);
              if (isTerminalAgentEvent(event)) break;
            }
            if (terminalRef.current) break;
            reconnectAttempt += 1;
          } catch (error) {
            if (controller.signal.aborted) return;
            if (
              error instanceof AgentApiError ||
              error instanceof AgentAuthenticationError ||
              error instanceof AgentApiProtocolError
            ) {
              throw error;
            }
            setErrorMessage(describeError(error, t));
            if (error instanceof AgentSseProtocolError) {
              const replay = await client.listEvents(
                currentRunId,
                cursorRef.current,
                controller.signal,
              );
              for (const raw of replay) {
                applyEvent(eventResponseToTimeline(raw));
              }
            }
            reconnectAttempt += 1;
          }
          const backoff = Math.min(5_000, 250 * 2 ** Math.min(reconnectAttempt, 5));
          await abortableDelay(backoff, controller.signal);
        }
        if (!controller.signal.aborted) {
          setStreamState("closed");
          const finalStatus = await client.getRun(currentRunId, controller.signal);
          setRunDetails(finalStatus);
          setRunStatus(finalStatus.status);
          if (finalStatus.status === "succeeded") {
            const visibleArtifacts = await client.listArtifacts(currentRunId, controller.signal);
            setArtifacts(visibleArtifacts);
            const answerArtifact = visibleArtifacts.find((artifact) => artifact.kind === "answer");
            if (answerArtifact !== undefined) {
              setIsPreviewLoading(true);
              try {
                setArtifactPreview(await client.getArtifactPreview(answerArtifact.artifactId, controller.signal));
              } finally {
                setIsPreviewLoading(false);
              }
            }
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setStreamState("closed");
          setErrorMessage(describeError(error, t));
        }
      }
    })();
    return () => controller.abort();
  }, [applyEvent, client, recoveryStore, runId, t]);

  useEffect(() => {
    const approvalId = events.find((event) => event.eventType === "run.waiting_approval")?.payload.approval_id;
    if (typeof approvalId !== "string") return;
    const controller = new AbortController();
    void client
      .getApproval(approvalId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setApproval(value);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setErrorMessage(describeError(error, t));
      });
    return () => controller.abort();
  }, [approvalRefresh, client, events, t]);

  const handleCreate = async (): Promise<void> => {
    if (readOnly || !canCreateRuns) return;
    setFormError(null);
    setErrorMessage(null);
    const selected = documents.find((document) => document.versionId === selectedDocumentId);
    if (selected === undefined) {
      setFormError(t("agent.error.chooseDocument"));
      return;
    }
    if (inputText.trim() === "") {
      setFormError(t("agent.error.enterRequest"));
      return;
    }
    let extractionSchema: Record<string, unknown> | null | undefined;
    if (taskType === "structured_extraction") {
      try {
        const parsed: unknown = JSON.parse(extractionSchemaText);
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error("schema");
        extractionSchema = parsed as Record<string, unknown>;
      } catch {
        setFormError(t("agent.error.invalidSchema"));
        return;
      }
    }
    const request: CreateAgentRunRequest = {
      documentVersionId: selected.versionId,
      taskType,
      inputText: inputText.trim(),
      extractionSchema,
      publishRequested,
    };
    setIsCreating(true);
    try {
      const created = await client.createRun(request, dependencies.idempotencyKeyFactory());
      recoveryStore.save({ version: 1, runId: created.runId, lastSequence: 0 });
      cursorRef.current = 0;
      setLastSequence(0);
      setRunId(created.runId);
      setInputText("");
      setRunDetails(null);
      setApproval(null);
      setArtifacts([]);
      setArtifactPreview(null);
    } catch (error) {
      setErrorMessage(describeError(error, t));
    } finally {
      setIsCreating(false);
    }
  };

  const handleCancel = async (): Promise<void> => {
    if (readOnly) return;
    if (runId === null) return;
    setIsCanceling(true);
    setErrorMessage(null);
    try {
      const result = await client.cancelRun(runId);
      setRunStatus(result.status);
    } catch (error) {
      setErrorMessage(describeError(error, t));
    } finally {
      setIsCanceling(false);
    }
  };

  const handleDecision = async (decision: "approved" | "rejected"): Promise<void> => {
    if (readOnly) return;
    if (approval === null || runId === null || !canDecideApproval || !approval.canDecide) return;
    setDecisionState(decision === "approved" ? "approving" : "rejecting");
    setErrorMessage(null);
    try {
      const result = await client.decideApproval(
        approval.approvalId,
        {
          decision,
          operation: approval.operation,
          targetResourceType: approval.targetResourceType,
          targetResourceId: approval.targetResourceId,
          targetDocumentVersionId: approval.targetDocumentVersionId,
          targetFingerprint: approval.targetFingerprint,
          comment: null,
        },
        dependencies.idempotencyKeyFactory(),
      );
      setApproval((current) => {
        if (current === null || !approvalStatuses.has(result.status as ApprovalRequestResponse["status"])) {
          return current;
        }
        return {
          ...current,
          status: result.status as ApprovalRequestResponse["status"],
          decidedAt: result.decidedAt,
          canDecide: false,
        };
      });
      setApprovalRefresh((current) => current + 1);
      const refreshedRun = await client.getRun(runId);
      setRunDetails(refreshedRun);
      setRunStatus(refreshedRun.status);
    } catch (error) {
      setErrorMessage(describeError(error, t));
    } finally {
      setDecisionState("idle");
    }
  };

  const handleDownload = async (artifactId: string): Promise<void> => {
    if (readOnly) return;
    setDownloadingArtifactId(artifactId);
    setErrorMessage(null);
    try {
      const result = await client.getArtifactDownload(artifactId);
      dependencies.openExternal(result.url);
    } catch (error) {
      setErrorMessage(describeError(error, t));
    } finally {
      setDownloadingArtifactId(null);
    }
  };

  const clearRun = (): void => {
    if (readOnly) return;
    recoveryStore.clear();
    setRunId(null);
    setRunStatus(null);
    setRunDetails(null);
    setEvents([]);
    setApproval(null);
    setArtifacts([]);
    setArtifactPreview(null);
    setLastSequence(0);
    cursorRef.current = 0;
  };

  const hasToken = readOnly || tokenStore.load() !== null;
  const canCancel = !readOnly && runId !== null && runStatus !== null && !terminalStatuses.has(runStatus);

  return (
    <section className={readOnly ? "agent-workspace showcase-agent-workspace" : "agent-workspace"} aria-labelledby="agent-title">
      <div className="agent-heading">
        <div>
          <p className="eyebrow">{t("agent.eyebrow")}</p>
          <h1 id="agent-title">{t("agent.title")}</h1>
        </div>
        <div className="agent-heading-actions">
          <span className={`token-state ${readOnly ? "showcase" : hasToken ? "connected" : "disconnected"}`}>
            <ShieldCheck aria-hidden="true" />
            {readOnly ? t("showcase.pill") : hasToken ? t("agent.authenticated") : t("agent.tokenRequired")}
          </span>
          {runId !== null && !readOnly && (
            <button className="icon-button" type="button" aria-label={t("agent.clearRun")} title={t("agent.clearRun")} onClick={clearRun}>
              <X aria-hidden="true" />
            </button>
          )}
        </div>
      </div>

      {!readOnly && !hasToken && (
        <div className="agent-auth-hint" role="status">
          <ShieldCheck aria-hidden="true" />
          <span><strong>{t("agent.connectTitle")}</strong><small>{t("agent.connectDetail")}</small></span>
          {openDocuments !== undefined && (
            <button className="secondary-button" type="button" onClick={openDocuments}>
              <Files aria-hidden="true" />{t("agent.openDocuments")}
            </button>
          )}
        </div>
      )}

      <div className="agent-grid">
        <form className="agent-form" onSubmit={(event) => { event.preventDefault(); void handleCreate(); }}>
          <div className="section-heading">
            <h2>{t("agent.startRun")}</h2>
            <span>{t("agent.readyVersions", { value: String(documents.length) })}</span>
          </div>
          <label className="field-label" htmlFor="agent-document">{t("agent.documentVersion")}</label>
          <select
            id="agent-document"
            value={selectedDocumentId}
            onChange={(event) => setSelectedDocumentId(event.target.value)}
            disabled={readOnly || isCreating || documents.length === 0 || !canCreateRuns}
          >
            <option value="">{t("agent.selectReadyVersion")}</option>
            {documents.map((document) => (
              <option key={document.versionId} value={document.versionId}>
                {document.filename} · {formatBytes(document.sizeBytes)}
              </option>
            ))}
          </select>

          <fieldset className="task-selector">
            <legend className="field-label">{t("agent.taskType")}</legend>
            <div className="segmented-control" role="radiogroup" aria-label={t("agent.taskType")}>
              {agentRunTaskTypeSchema.options.map((option) => (
                <button
                  key={option}
                  type="button"
                  role="radio"
                  aria-checked={taskType === option}
                  className={taskType === option ? "segment active" : "segment"}
                  disabled={readOnly || !canCreateRuns}
                  onClick={() => setTaskType(option)}
                >
                  {t(taskLabelKeys[option])}
                </button>
              ))}
            </div>
          </fieldset>

          <label className="field-label" htmlFor="agent-request">{t("agent.request")}</label>
          <textarea
            id="agent-request"
            value={inputText}
            onChange={(event) => setInputText(event.target.value)}
            rows={5}
            maxLength={20_000}
            placeholder={t("agent.requestPlaceholder")}
            disabled={readOnly || !canCreateRuns}
          />

          {taskType === "structured_extraction" && (
            <>
              <label className="field-label" htmlFor="agent-schema">{t("agent.extractionSchema")}</label>
              <textarea id="agent-schema" value={extractionSchemaText} onChange={(event) => setExtractionSchemaText(event.target.value)} rows={3} disabled={readOnly || !canCreateRuns} />
            </>
          )}

          <label className="check-row">
            <input type="checkbox" checked={publishRequested} onChange={(event) => setPublishRequested(event.target.checked)} disabled={readOnly || !canCreateRuns} />
            <span>{t("agent.requestPublication")}</span>
          </label>

          {!readOnly && !canCreateRuns && <p className="permission-notice"><ShieldCheck aria-hidden="true" />{t("permissions.runDenied")}</p>}
          {formError !== null && <p className="agent-alert" role="alert"><CircleAlert aria-hidden="true" />{formError}</p>}
          <button className="command-button agent-submit" type="submit" disabled={readOnly || isCreating || !hasToken || !canCreateRuns} title={readOnly ? t("showcase.detail") : !canCreateRuns ? t("permissions.writeDenied") : undefined}>
            {isCreating ? <LoaderCircle className="spin" aria-hidden="true" /> : <Play aria-hidden="true" />}
            {isCreating ? t("agent.creating") : readOnly ? t("documents.demoOnly") : t("agent.createRun")}
          </button>
        </form>

        <div className="agent-run-panel">
          <div className="section-heading">
            <h2>{t("agent.currentRun")}</h2>
            {runId !== null && <span className={`run-status status-${runStatus ?? "pending"}`}>{runStatus ? t(statusLabelKeys[runStatus]) : t("agent.loading")}</span>}
          </div>
          {runId === null ? (
            <div className="agent-empty"><FileCheck2 aria-hidden="true" /><p>{t("agent.noRun")}</p></div>
          ) : (
            <>
              <div className="run-meta">
                <span>{t("agent.run")} <strong>{shortId(runId)}</strong></span>
                <span>{t("agent.cursor")} <strong>{lastSequence}</strong></span>
                <span className={`stream-state ${streamState}`}>{t(streamLabelKeys[streamState])}</span>
              </div>
              {runDetails !== null && (
                <section className="execution-meta" aria-labelledby="execution-meta-title">
                  <div className="section-heading">
                    <h3 id="execution-meta-title">{t("agent.executionMetadata")}</h3>
                    <span>{t("agent.executions", { value: String(runDetails.executions.length), suffix: runDetails.executions.length === 1 ? "" : "s" })}</span>
                  </div>
                  <dl className="execution-meta-grid">
                    <div><dt>{t("agent.model")}</dt><dd>{formatModel(runDetails)}</dd></div>
                    <div><dt>{t("agent.tokenUsage")}</dt><dd>{formatCount(runDetails.totalTokens, t)} {t("agent.total")} <span>({formatCount(runDetails.promptTokens, t)} {t("agent.input")} · {formatCount(runDetails.completionTokens, t)} {t("agent.output")})</span></dd></div>
                    <div><dt>{t("agent.providerCalls")}</dt><dd>{formatCount(runDetails.providerRequestCount, t)} {t("agent.requests")} <span>({formatCount(runDetails.providerUsageRequestCount, t)} {t("agent.metered")})</span></dd></div>
                    <div><dt>{t("agent.recovery")}</dt><dd>{formatCount(runDetails.fallbackCount, t)} {t("agent.fallback")} · {formatCount(runDetails.repairRequestCount, t)} {t("agent.repair")}</dd></div>
                    <div><dt>{t("agent.breaker")}</dt><dd><span className={`breaker-state breaker-${runDetails.breakerState ?? "unknown"}`}>{runDetails.breakerState ?? t("agent.notReported")}</span>{runDetails.fallbackTriggerCode !== null && <span> · {runDetails.fallbackTriggerCode}</span>}</dd></div>
                    <div><dt>{t("agent.executionSequence")}</dt><dd>{runDetails.currentExecutionSeq.toLocaleString()} <span>{t("agent.of")} {runDetails.executions.length.toLocaleString()}</span></dd></div>
                  </dl>
                </section>
              )}
              <div className="timeline" aria-live="polite">
                {events.length === 0 && (
                  streamState === "closed"
                    ? <div className="timeline-loading">{t("agent.noHistory")}</div>
                    : <div className="timeline-loading"><LoaderCircle className="spin" aria-hidden="true" />{t("agent.loadingEvents")}</div>
                )}
                {events.map((event) => {
                  const Icon = eventIcon(event.eventType);
                  return (
                    <article className="timeline-item" key={event.seq}>
                      <div className="timeline-marker"><Icon aria-hidden="true" /></div>
                      <div className="timeline-copy">
                        <strong>{eventLabel(event, t)}</strong>
                        <span>{formatDate(event.createdAt, locale)}</span>
                      </div>
                      <span className="timeline-seq">#{event.seq}</span>
                    </article>
                  );
                })}
              </div>

              {approval !== null && runStatus === "waiting_approval" && (
                <section className="approval-panel" aria-labelledby="approval-title">
                  <div className="section-heading"><h3 id="approval-title">{t("agent.approvalRequest")}</h3><span>{t(approvalStatusLabelKeys[approval.status])}</span></div>
                  <dl className="approval-details">
                    <div><dt>{t("agent.target")}</dt><dd>{shortId(approval.targetResourceId)}</dd></div>
                    <div><dt>{t("agent.fingerprint")}</dt><dd>{shortId(approval.targetFingerprint)}</dd></div>
                    <div><dt>{t("agent.expires")}</dt><dd>{formatDate(approval.expiresAt, locale)}</dd></div>
                  </dl>
                  {approval.canDecide && canDecideApproval && approval.status === "pending" ? (
                    <div className="approval-actions">
                      <button className="command-button" type="button" disabled={decisionState !== "idle"} onClick={() => void handleDecision("approved")}>
                        {decisionState === "approving" ? <LoaderCircle className="spin" aria-hidden="true" /> : <Check aria-hidden="true" />}{t("agent.approve")}
                      </button>
                      <button className="icon-button danger-icon" type="button" aria-label={t("agent.rejectApproval")} title={t("agent.rejectApproval")} disabled={decisionState !== "idle"} onClick={() => void handleDecision("rejected")}>
                        {decisionState === "rejecting" ? <LoaderCircle className="spin" aria-hidden="true" /> : <Ban aria-hidden="true" />}
                      </button>
                    </div>
                  ) : <p className="muted-copy">{!canDecideApproval ? t("permissions.approvalDenied") : t("agent.approvalNoAction")}</p>}
                </section>
              )}

              {artifacts.length > 0 && (
                <section className="artifact-panel" aria-labelledby="artifact-title">
                  <div className="section-heading"><h3 id="artifact-title">{t("agent.verifiedResult")}</h3><span>{t("agent.artifacts", { value: String(artifacts.length), suffix: artifacts.length === 1 ? "" : "s" })}</span></div>
                  {isPreviewLoading && (
                    <div className="artifact-preview-loading" role="status"><LoaderCircle className="spin" aria-hidden="true" />{t("agent.loadingAnswer")}</div>
                  )}
                  {artifactPreview !== null && (
                    <article className="answer-review" aria-label={t("agent.answerReview")}>
                      <header className="answer-review-header">
                        <span className="answer-review-icon"><FileText aria-hidden="true" /></span>
                        <div><p className="eyebrow">{t("agent.groundedAnswer")}</p><h3>{t("agent.result", { value: t(taskLabelKeys[artifactPreview.taskType]) })}</h3></div>
                        <span className={`risk-pill risk-${artifactPreview.riskHint ?? "unknown"}`}>{riskLabel(artifactPreview.riskHint, t)}</span>
                      </header>
                      <div className="answer-copy">{artifactPreview.answerText}</div>
                      {artifactPreview.structuredFields !== null && (
                        <details className="structured-result"><summary>{t("agent.structuredFields")}</summary><pre>{JSON.stringify(artifactPreview.structuredFields, null, 2)}</pre></details>
                      )}
                      <section className="citation-review" aria-labelledby="citation-review-title">
                        <div className="section-heading"><h3 id="citation-review-title">{t("agent.evidenceCitations")}</h3><span>{t("agent.verified", { value: String(artifactPreview.citations.length) })}</span></div>
                        <ol className="citation-list">
                          {artifactPreview.citations.map((citation, index) => (
                            <li key={citation.chunkId}>
                              <span className="citation-index">{index + 1}</span>
                              <div><strong>{citationLocation(citation, t)}</strong><blockquote>{citation.excerpt}</blockquote><code>{shortId(citation.chunkId)}</code></div>
                            </li>
                          ))}
                        </ol>
                      </section>
                      <footer className="grounding-meta">
                        <span>{t("agent.graph")} <strong>{artifactPreview.behaviorVersions.graphVersion}</strong></span>
                        <span>{t("agent.prompt")} <strong>{artifactPreview.behaviorVersions.promptVersion}</strong></span>
                        <span>{t("agent.toolSchema")} <strong>{artifactPreview.behaviorVersions.toolSchemaVersion}</strong></span>
                        <span>SHA-256 <strong>{shortId(artifactPreview.contentSha256)}</strong></span>
                      </footer>
                    </article>
                  )}
                  {artifacts.map((artifact) => (
                    <div className="artifact-row" key={artifact.artifactId}>
                      <div><strong>{artifactKindLabel(artifact.kind, t)}</strong><span>{formatBytes(artifact.sizeBytes)} · {artifactStatusLabel(artifact.status, t)}</span></div>
                      <button className="icon-button primary-icon" type="button" aria-label={t("agent.downloadArtifact", { value: artifactKindLabel(artifact.kind, t) })} title={readOnly ? t("agent.downloadUnavailable") : t("agent.download")} disabled={readOnly || downloadingArtifactId !== null} onClick={() => void handleDownload(artifact.artifactId)}>
                        {downloadingArtifactId === artifact.artifactId ? <LoaderCircle className="spin" aria-hidden="true" /> : <Download aria-hidden="true" />}
                      </button>
                    </div>
                  ))}
                </section>
              )}

              <div className="run-actions">
                <button className="icon-button" type="button" aria-label={t("agent.refreshRun")} title={t("agent.refreshRun")} disabled={streamState === "loading"} onClick={() => { setRunId(null); window.setTimeout(() => setRunId(runId), 0); }}>
                  <RefreshCw aria-hidden="true" />
                </button>
                {canCancel && <button className="icon-button danger-icon" type="button" aria-label={t("agent.cancelRun")} title={t("agent.cancelRun")} disabled={isCanceling} onClick={() => void handleCancel()}>
                  {isCanceling ? <LoaderCircle className="spin" aria-hidden="true" /> : <Square aria-hidden="true" />}
                </button>}
              </div>
            </>
          )}
        </div>
      </div>

      {errorMessage !== null && <p className="agent-alert" role="alert"><CircleAlert aria-hidden="true" />{errorMessage}</p>}
    </section>
  );
}
