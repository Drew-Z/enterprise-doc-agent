import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Ban,
  Check,
  CircleAlert,
  CircleCheck,
  CircleX,
  Download,
  FileCheck2,
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
  type AgentRunTaskType,
  type ApprovalRequestResponse,
  type CreateAgentRunRequest,
  type ReadyDocumentVersion,
} from "./api/schemas";
import { createUploadTokenStore } from "../upload/persistence";
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

const taskLabels: Record<AgentRunTaskType, string> = {
  question_answer: "Question",
  summary: "Summary",
  structured_extraction: "Extraction",
};

const statusLabels: Record<AgentRunStatus, string> = {
  pending: "Queued",
  running: "Running",
  waiting_approval: "Waiting for approval",
  succeeded: "Complete",
  refused: "Refused",
  failed: "Failed",
  cancelled: "Canceled",
  rejected: "Rejected",
  expired: "Expired",
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

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function shortId(value: string): string {
  return `${value.slice(0, 8)}...${value.slice(-6)}`;
}

function describeError(error: unknown): string {
  if (error instanceof AgentAuthenticationError) return "Save a local API token in Documents before starting a run.";
  if (error instanceof AgentApiError) return `${error.message} (${error.code})`;
  if (error instanceof AgentApiProtocolError) return error.message;
  if (error instanceof AgentNetworkError) return "The Agent API could not be reached.";
  if (error instanceof Error) return error.message;
  return "The Agent request failed.";
}

function eventLabel(event: AgentTimelineEvent): string {
  switch (event.eventType) {
    case "run.created":
      return "Run created";
    case "run.started":
      return "Worker started";
    case "run.resumed":
      return "Run resumed";
    case "run.waiting_approval":
      return "Approval requested";
    case "run.cancel_requested":
      return "Cancellation requested";
    case "run.cancelled":
      return "Run canceled";
    case "run.finished":
      return `Run ${String(event.payload.status)}`;
  }
}

function eventIcon(eventType: AgentTimelineEvent["eventType"]) {
  if (eventType === "run.finished") return CircleCheck;
  if (eventType === "run.cancelled") return CircleX;
  if (eventType === "run.waiting_approval") return ShieldCheck;
  return RotateCw;
}

export function AgentWorkspace({
  dependencies = defaultDependencies,
  tokenStorage = sessionStorage,
  recoveryStorage = localStorage,
}: AgentWorkspaceProps) {
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
  const [runId, setRunId] = useState<string | null>(recovered?.runId ?? null);
  const [lastSequence, setLastSequence] = useState(recovered?.lastSequence ?? 0);
  const [events, setEvents] = useState<AgentTimelineEvent[]>([]);
  const [runStatus, setRunStatus] = useState<AgentRunStatus | null>(null);
  const [approval, setApproval] = useState<ApprovalRequestResponse | null>(null);
  const [artifacts, setArtifacts] = useState<Awaited<ReturnType<AgentApiClientProtocol["listArtifacts"]>>>([]);
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
        if (runId !== null) {
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
    [recoveryStore, runId],
  );

  useEffect(() => {
    const controller = new AbortController();
    setErrorMessage(null);
    void (async () => {
      try {
        const result = await client.listReadyDocumentVersions(controller.signal);
        if (!controller.signal.aborted) setDocuments(result);
      } catch (error) {
        if (!controller.signal.aborted) setErrorMessage(describeError(error));
      }
    })();
    return () => controller.abort();
  }, [client]);

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
    setApproval(null);
    setArtifacts([]);
    setStreamState("loading");
    void (async () => {
      let reconnectAttempt = 0;
      try {
        const status = await client.getRun(currentRunId, controller.signal);
        if (controller.signal.aborted) return;
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
          setRunStatus(finalStatus.status);
          if (finalStatus.status === "succeeded") {
            setArtifacts(await client.listArtifacts(currentRunId, controller.signal));
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
            setErrorMessage(describeError(error));
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
          setRunStatus(finalStatus.status);
          if (finalStatus.status === "succeeded") {
            setArtifacts(await client.listArtifacts(currentRunId, controller.signal));
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setStreamState("closed");
          setErrorMessage(describeError(error));
        }
      }
    })();
    return () => controller.abort();
  }, [applyEvent, client, recoveryStore, runId]);

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
        if (!controller.signal.aborted) setErrorMessage(describeError(error));
      });
    return () => controller.abort();
  }, [approvalRefresh, client, events]);

  const handleCreate = async (): Promise<void> => {
    setFormError(null);
    setErrorMessage(null);
    const selected = documents.find((document) => document.versionId === selectedDocumentId);
    if (selected === undefined) {
      setFormError("Choose a ready document version.");
      return;
    }
    if (inputText.trim() === "") {
      setFormError("Enter a task request.");
      return;
    }
    let extractionSchema: Record<string, unknown> | null | undefined;
    if (taskType === "structured_extraction") {
      try {
        const parsed: unknown = JSON.parse(extractionSchemaText);
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error("schema");
        extractionSchema = parsed as Record<string, unknown>;
      } catch {
        setFormError("Extraction schema must be a JSON object.");
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
      setApproval(null);
      setArtifacts([]);
    } catch (error) {
      setErrorMessage(describeError(error));
    } finally {
      setIsCreating(false);
    }
  };

  const handleCancel = async (): Promise<void> => {
    if (runId === null) return;
    setIsCanceling(true);
    setErrorMessage(null);
    try {
      const result = await client.cancelRun(runId);
      setRunStatus(result.status);
    } catch (error) {
      setErrorMessage(describeError(error));
    } finally {
      setIsCanceling(false);
    }
  };

  const handleDecision = async (decision: "approved" | "rejected"): Promise<void> => {
    if (approval === null || runId === null || !approval.canDecide) return;
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
      setRunStatus(refreshedRun.status);
    } catch (error) {
      setErrorMessage(describeError(error));
    } finally {
      setDecisionState("idle");
    }
  };

  const handleDownload = async (artifactId: string): Promise<void> => {
    setDownloadingArtifactId(artifactId);
    setErrorMessage(null);
    try {
      const result = await client.getArtifactDownload(artifactId);
      dependencies.openExternal(result.url);
    } catch (error) {
      setErrorMessage(describeError(error));
    } finally {
      setDownloadingArtifactId(null);
    }
  };

  const clearRun = (): void => {
    recoveryStore.clear();
    setRunId(null);
    setRunStatus(null);
    setEvents([]);
    setApproval(null);
    setArtifacts([]);
    setLastSequence(0);
    cursorRef.current = 0;
  };

  const hasToken = tokenStore.load() !== null;
  const canCancel = runId !== null && runStatus !== null && !terminalStatuses.has(runStatus);

  return (
    <section className="agent-workspace" aria-labelledby="agent-title">
      <div className="agent-heading">
        <div>
          <p className="eyebrow">Durable execution</p>
          <h1 id="agent-title">Agent run workspace</h1>
        </div>
        <div className="agent-heading-actions">
          <span className={`token-state ${hasToken ? "connected" : "disconnected"}`}>
            <ShieldCheck aria-hidden="true" />
            {hasToken ? "Authenticated" : "Token required"}
          </span>
          {runId !== null && (
            <button className="icon-button" type="button" aria-label="Clear run" title="Clear run" onClick={clearRun}>
              <X aria-hidden="true" />
            </button>
          )}
        </div>
      </div>

      <div className="agent-grid">
        <form className="agent-form" onSubmit={(event) => { event.preventDefault(); void handleCreate(); }}>
          <div className="section-heading">
            <h2>Start a run</h2>
            <span>{documents.length} ready versions</span>
          </div>
          <label className="field-label" htmlFor="agent-document">Document version</label>
          <select
            id="agent-document"
            value={selectedDocumentId}
            onChange={(event) => setSelectedDocumentId(event.target.value)}
            disabled={isCreating || documents.length === 0}
          >
            <option value="">Select a ready version</option>
            {documents.map((document) => (
              <option key={document.versionId} value={document.versionId}>
                {document.filename} · {formatBytes(document.sizeBytes)}
              </option>
            ))}
          </select>

          <fieldset className="task-selector">
            <legend className="field-label">Task type</legend>
            <div className="segmented-control" role="radiogroup" aria-label="Task type">
              {agentRunTaskTypeSchema.options.map((option) => (
                <button
                  key={option}
                  type="button"
                  role="radio"
                  aria-checked={taskType === option}
                  className={taskType === option ? "segment active" : "segment"}
                  onClick={() => setTaskType(option)}
                >
                  {taskLabels[option]}
                </button>
              ))}
            </div>
          </fieldset>

          <label className="field-label" htmlFor="agent-request">Request</label>
          <textarea
            id="agent-request"
            value={inputText}
            onChange={(event) => setInputText(event.target.value)}
            rows={5}
            maxLength={20_000}
            placeholder="Ask a grounded question about the selected document"
          />

          {taskType === "structured_extraction" && (
            <>
              <label className="field-label" htmlFor="agent-schema">Extraction schema</label>
              <textarea id="agent-schema" value={extractionSchemaText} onChange={(event) => setExtractionSchemaText(event.target.value)} rows={3} />
            </>
          )}

          <label className="check-row">
            <input type="checkbox" checked={publishRequested} onChange={(event) => setPublishRequested(event.target.checked)} />
            <span>Request publication after approval</span>
          </label>

          {formError !== null && <p className="agent-alert" role="alert"><CircleAlert aria-hidden="true" />{formError}</p>}
          <button className="command-button agent-submit" type="submit" disabled={isCreating || !hasToken}>
            {isCreating ? <LoaderCircle className="spin" aria-hidden="true" /> : <Play aria-hidden="true" />}
            {isCreating ? "Creating" : "Create run"}
          </button>
        </form>

        <div className="agent-run-panel">
          <div className="section-heading">
            <h2>Current run</h2>
            {runId !== null && <span className={`run-status status-${runStatus ?? "pending"}`}>{runStatus ? statusLabels[runStatus] : "Loading"}</span>}
          </div>
          {runId === null ? (
            <div className="agent-empty"><FileCheck2 aria-hidden="true" /><p>No Agent run selected.</p></div>
          ) : (
            <>
              <div className="run-meta">
                <span>Run <strong>{shortId(runId)}</strong></span>
                <span>Cursor <strong>{lastSequence}</strong></span>
                <span className={`stream-state ${streamState}`}>{streamState}</span>
              </div>
              <div className="timeline" aria-live="polite">
                {events.length === 0 && <div className="timeline-loading"><LoaderCircle className="spin" aria-hidden="true" />Loading events</div>}
                {events.map((event) => {
                  const Icon = eventIcon(event.eventType);
                  return (
                    <article className="timeline-item" key={event.seq}>
                      <div className="timeline-marker"><Icon aria-hidden="true" /></div>
                      <div className="timeline-copy">
                        <strong>{eventLabel(event)}</strong>
                        <span>{formatDate(event.createdAt)}</span>
                      </div>
                      <span className="timeline-seq">#{event.seq}</span>
                    </article>
                  );
                })}
              </div>

              {approval !== null && runStatus === "waiting_approval" && (
                <section className="approval-panel" aria-labelledby="approval-title">
                  <div className="section-heading"><h3 id="approval-title">Approval request</h3><span>{approval.status}</span></div>
                  <dl className="approval-details">
                    <div><dt>Target</dt><dd>{shortId(approval.targetResourceId)}</dd></div>
                    <div><dt>Fingerprint</dt><dd>{shortId(approval.targetFingerprint)}</dd></div>
                    <div><dt>Expires</dt><dd>{formatDate(approval.expiresAt)}</dd></div>
                  </dl>
                  {approval.canDecide && approval.status === "pending" ? (
                    <div className="approval-actions">
                      <button className="command-button" type="button" disabled={decisionState !== "idle"} onClick={() => void handleDecision("approved")}>
                        {decisionState === "approving" ? <LoaderCircle className="spin" aria-hidden="true" /> : <Check aria-hidden="true" />}Approve
                      </button>
                      <button className="icon-button danger-icon" type="button" aria-label="Reject approval" title="Reject approval" disabled={decisionState !== "idle"} onClick={() => void handleDecision("rejected")}>
                        {decisionState === "rejecting" ? <LoaderCircle className="spin" aria-hidden="true" /> : <Ban aria-hidden="true" />}
                      </button>
                    </div>
                  ) : <p className="muted-copy">This approval is no longer actionable for the current principal.</p>}
                </section>
              )}

              {artifacts.length > 0 && (
                <section className="artifact-panel" aria-labelledby="artifact-title">
                  <div className="section-heading"><h3 id="artifact-title">Verified artifacts</h3><span>{artifacts.length}</span></div>
                  {artifacts.map((artifact) => (
                    <div className="artifact-row" key={artifact.artifactId}>
                      <div><strong>{artifact.kind}</strong><span>{formatBytes(artifact.sizeBytes)} · {artifact.status}</span></div>
                      <button className="icon-button primary-icon" type="button" aria-label={`Download ${artifact.kind}`} title="Download artifact" disabled={downloadingArtifactId !== null} onClick={() => void handleDownload(artifact.artifactId)}>
                        {downloadingArtifactId === artifact.artifactId ? <LoaderCircle className="spin" aria-hidden="true" /> : <Download aria-hidden="true" />}
                      </button>
                    </div>
                  ))}
                </section>
              )}

              <div className="run-actions">
                <button className="icon-button" type="button" aria-label="Refresh run" title="Refresh run" disabled={streamState === "loading"} onClick={() => { setRunId(null); window.setTimeout(() => setRunId(runId), 0); }}>
                  <RefreshCw aria-hidden="true" />
                </button>
                {canCancel && <button className="icon-button danger-icon" type="button" aria-label="Cancel run" title="Cancel run" disabled={isCanceling} onClick={() => void handleCancel()}>
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
