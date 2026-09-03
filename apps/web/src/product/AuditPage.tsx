import { useInfiniteQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Activity, ArrowRight, CalendarDays, CircleAlert, Download, Filter, LoaderCircle, RefreshCw, Search, ShieldCheck, UserRound } from "lucide-react";

import { createUploadTokenStore } from "../upload/persistence";
import { formatApiError } from "../api/errorDisplay";
import type { ProductRoute } from "./routes";
import { exportAuditEvents, fetchAuditEvents, type AuditEvent } from "./auditApi";
import { AuditGovernancePanel } from "./AuditGovernancePanel";
import { showcaseAuditEvents } from "./auditData";
import { type MessageKey, useT, useLocale } from "../i18n";

interface AuditPageProps {
  navigate: (route: ProductRoute) => void;
  showcaseMode?: boolean;
  canExport?: boolean;
  canManageGovernance?: boolean;
}

const actionOptions = [
  { value: "", label: "audit.allActions" },
  { value: "document.upload_completed", label: "audit.action.documentUploaded" },
  { value: "job.succeeded", label: "audit.action.jobSucceeded" },
  { value: "agent_run.created", label: "audit.action.agentRunCreated" },
  { value: "approval.approved", label: "audit.action.approvalApproved" },
  { value: "artifact.published", label: "audit.action.artifactPublished" },
  { value: "agent_run.finished", label: "audit.action.agentRunFinished" },
  { value: "audit.retention_policy.updated", label: "audit.action.retentionPolicyUpdated" },
  { value: "audit.legal_hold.created", label: "audit.action.legalHoldCreated" },
  { value: "audit.legal_hold.released", label: "audit.action.legalHoldReleased" },
] as const;

const resourceOptions = [
  { value: "", label: "audit.allResources" },
  { value: "document", label: "audit.resource.documents" },
  { value: "job", label: "audit.resource.jobs" },
  { value: "agent_run", label: "audit.resource.agentRuns" },
  { value: "approval", label: "audit.resource.approvals" },
  { value: "artifact", label: "audit.resource.artifacts" },
] as const;

function formatDate(value: string, locale: "en" | "zh"): string {
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function shortId(value: string | null, t: ReturnType<typeof useT>): string {
  return value ? value.slice(0, 8) : t("audit.system");
}

function actionLabel(value: string, t: ReturnType<typeof useT>): string {
  const labels: Record<string, MessageKey> = {
    "document.upload_completed": "audit.action.documentUploaded",
    "job.succeeded": "audit.action.jobSucceeded",
    "agent_run.created": "audit.action.agentRunCreated",
    "approval.approved": "audit.action.approvalApproved",
    "artifact.published": "audit.action.artifactPublished",
    "agent_run.finished": "audit.action.agentRunFinished",
    "audit.retention_policy.updated": "audit.action.retentionPolicyUpdated",
    "audit.legal_hold.created": "audit.action.legalHoldCreated",
    "audit.legal_hold.released": "audit.action.legalHoldReleased",
  };
  const key = labels[value];
  if (key !== undefined) return t(key);
  return value.replaceAll("_", " ").replaceAll(".", " / ");
}

function normalizeSearch(value: string): string {
  return value.toLowerCase().replace(/[._/]+/g, " ").replace(/\s+/g, " ").trim();
}

function resourceRoute(event: AuditEvent): ProductRoute | null {
  if (event.resourceType === "document") return "documents";
  if (event.resourceType === "agent_run") return "agent-runs";
  return null;
}

function eventSummary(event: AuditEvent, t: ReturnType<typeof useT>): string {
  const metadata = event.metadata;
  if (typeof metadata.filename === "string") return metadata.filename;
  if (typeof metadata.status === "string") return t("audit.eventStatus", { value: metadata.status });
  if (typeof metadata.operation === "string") return t("audit.eventOperation", { value: metadata.operation });
  if (typeof metadata.job_type === "string") return metadata.job_type;
  return t("audit.eventRecorded");
}

function EventIcon({ event }: { event: AuditEvent }) {
  if (event.resourceType === "agent_run") return <Activity aria-hidden="true" />;
  if (event.resourceType === "document") return <ShieldCheck aria-hidden="true" />;
  return <Filter aria-hidden="true" />;
}

export function AuditPage({ navigate, showcaseMode = false, canExport = true, canManageGovernance = false }: AuditPageProps) {
  const t = useT();
  const locale = useLocale();
  const tokenStore = useMemo(() => createUploadTokenStore(sessionStorage), []);
  const [query, setQuery] = useState("");
  const [action, setAction] = useState("");
  const [resourceType, setResourceType] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [isExporting, setIsExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const hasToken = showcaseMode || tokenStore.load() !== null;
  const audit = useInfiniteQuery({
    queryKey: ["audit-events", showcaseMode ? "showcase" : "live", action, resourceType, from, to],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => showcaseMode
      ? Promise.resolve({ items: showcaseAuditEvents, nextCursor: null })
      : fetchAuditEvents(tokenStore.load() ?? "", {
        action: action || undefined,
        resourceType: resourceType || undefined,
        from: from ? `${from}T00:00:00Z` : undefined,
        to: to ? `${to}T23:59:59Z` : undefined,
        cursor: pageParam ?? undefined,
      }, signal),
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
    enabled: hasToken,
    retry: false,
    staleTime: 10_000,
  });
  const events = useMemo(
    () => audit.data?.pages.flatMap((page) => page.items) ?? [],
    [audit.data?.pages],
  );
  const filteredEvents = useMemo(() => {
    const normalized = normalizeSearch(query);
    return events.filter((event) => {
      if (normalized === "") return true;
      const searchable = [
        event.action,
        actionLabel(event.action, t),
        event.resourceType,
        event.resourceId ?? "",
        eventSummary(event, t),
      ].map(normalizeSearch).join(" ");
      return searchable.includes(normalized);
    });
  }, [events, query, t]);
  const uniqueActors = new Set(filteredEvents.map((event) => event.actorId).filter(Boolean)).size;
  const uniqueResources = new Set(filteredEvents.map((event) => `${event.resourceType}:${event.resourceId}`)).size;

  async function handleExport() {
    if (!hasToken || showcaseMode || !canExport || isExporting) return;
    setIsExporting(true);
    setExportError(null);
    try {
      const blob = await exportAuditEvents(tokenStore.load() ?? "", {
        action: action || undefined,
        resourceType: resourceType || undefined,
        from: from ? `${from}T00:00:00Z` : undefined,
        to: to ? `${to}T23:59:59Z` : undefined,
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "audit-events.csv";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setExportError(error instanceof Error ? error.message : t("audit.exportError"));
    } finally {
      setIsExporting(false);
    }
  }

  return (
    <>
      <header className="product-page-header audit-page-header">
        <div>
          <p className="eyebrow">{t("audit.eyebrow")}</p>
          <h1>{t("audit.title")}</h1>
          <p className="page-summary">{t("audit.summary")}</p>
        </div>
        <div className="audit-source"><CalendarDays aria-hidden="true" /><span>{showcaseMode ? t("audit.fixture") : hasToken ? t("documents.currentTenant") : t("documents.notConnected")}</span></div>
      </header>

      <div className="audit-metrics" aria-label={t("audit.title")}>
        <div><span>{t("audit.eventsInView")}</span><strong>{hasToken && audit.data ? filteredEvents.length : "-"}</strong></div>
        <div><span>{t("audit.uniqueActors")}</span><strong>{hasToken && audit.data ? uniqueActors : "-"}</strong></div>
        <div><span>{t("audit.resources")}</span><strong>{hasToken && audit.data ? uniqueResources : "-"}</strong></div>
      </div>

      <AuditGovernancePanel showcaseMode={showcaseMode} canManage={canManageGovernance} />

      <section className="audit-section product-section" aria-labelledby="audit-title">
        <div className="audit-toolbar">
          <div><h2 id="audit-title">{t("audit.title")}</h2><p>{showcaseMode ? t("documents.showcaseDetail") : t("audit.summary")}</p></div>
          <div className="audit-toolbar-actions">
            <button className="secondary-button audit-export" type="button" aria-label={t("audit.exportLabel")} title={showcaseMode ? t("audit.exportUnavailable") : !canExport ? t("audit.exportOwnerOnly") : t("audit.export")} disabled={!hasToken || showcaseMode || !canExport || isExporting} onClick={() => void handleExport()}><Download aria-hidden="true" />{isExporting ? t("audit.exporting") : t("audit.export")}</button>
            <button className="icon-button" type="button" aria-label={t("audit.refresh")} title={t("audit.refresh")} disabled={!hasToken || audit.isFetching} onClick={() => void audit.refetch()}><RefreshCw aria-hidden="true" className={audit.isFetching ? "spin" : undefined} /></button>
          </div>
        </div>
        {!showcaseMode && hasToken && !canExport && <p className="permission-notice"><ShieldCheck aria-hidden="true" />{t("audit.exportOwnerOnly")}</p>}
        {exportError && <div className="audit-export-error" role="alert"><CircleAlert aria-hidden="true" /><span>{exportError}</span><button className="table-action" type="button" onClick={() => void handleExport()}>{t("documents.tryAgain")}</button></div>}
        <div className="audit-filters" role="group" aria-label={t("audit.filters")}>
          <label className="audit-search"><Search aria-hidden="true" /><input type="search" aria-label={t("audit.search")} placeholder={t("audit.search")} value={query} onChange={(event) => setQuery(event.target.value)} disabled={!hasToken} /></label>
          <select aria-label={t("audit.filterAction")} value={action} onChange={(event) => setAction(event.target.value)} disabled={!hasToken}>{actionOptions.map((option) => <option key={option.value} value={option.value}>{t(option.label)}</option>)}</select>
          <select aria-label={t("audit.filterResource")} value={resourceType} onChange={(event) => setResourceType(event.target.value)} disabled={!hasToken}>{resourceOptions.map((option) => <option key={option.value} value={option.value}>{t(option.label)}</option>)}</select>
          <label className="audit-date"><span>{t("audit.from")}</span><input aria-label={t("audit.startDate")} type="date" value={from} onChange={(event) => setFrom(event.target.value)} disabled={!hasToken} /></label>
          <label className="audit-date"><span>{t("audit.to")}</span><input aria-label={t("audit.endDate")} type="date" value={to} onChange={(event) => setTo(event.target.value)} disabled={!hasToken} /></label>
        </div>

        {!hasToken && <div className="inventory-empty"><ShieldCheck aria-hidden="true" /><div><h3>{t("documents.connect")}</h3><p>{t("documents.authRequired")}</p></div></div>}
        {hasToken && audit.isPending && <div className="inventory-empty" role="status"><LoaderCircle className="spin" aria-hidden="true" /><div><h3>{t("audit.title")}</h3><p>{t("documents.loadingDetail")}</p></div></div>}
        {hasToken && audit.isError && <div className="inventory-empty inventory-error" role="alert"><CircleAlert aria-hidden="true" /><div><h3>{t("audit.title")}</h3><p>{formatApiError(audit.error, t("audit.noEvents"), t("common.requestId"))}</p></div><button className="secondary-button" type="button" onClick={() => void audit.refetch()}>{t("documents.tryAgain")}</button></div>}
        {hasToken && audit.isSuccess && filteredEvents.length === 0 && <div className="inventory-empty"><Search aria-hidden="true" /><div><h3>{t("audit.noEvents")}</h3><p>{t("audit.summary")}</p></div></div>}
        {hasToken && audit.isSuccess && filteredEvents.length > 0 && (
          <>
            <div className="audit-table-wrap"><table className="audit-table"><thead><tr><th>{t("audit.event")}</th><th>{t("audit.actor")}</th><th>{t("audit.resource")}</th><th>{t("audit.occurred")}</th><th>{t("audit.trace")}</th><th><span className="sr-only">{t("audit.openResource")}</span></th></tr></thead><tbody>{filteredEvents.map((event) => { const destination = resourceRoute(event); return <tr key={event.eventId}><td><span className="audit-event-name"><span className="audit-event-icon"><EventIcon event={event} /></span><span><strong>{actionLabel(event.action, t)}</strong><small>{eventSummary(event, t)}</small></span></span></td><td><span className="audit-actor"><UserRound aria-hidden="true" />{shortId(event.actorId, t)}</span></td><td><code>{event.resourceType} · {shortId(event.resourceId, t)}</code></td><td>{formatDate(event.occurredAt, locale)}</td><td><span className="audit-trace"><code>{shortId(event.requestId, t)}</code><code>{shortId(event.correlationId, t)}</code></span></td><td>{destination && <button className="table-action" type="button" onClick={() => navigate(destination)}>{t("audit.open")} <ArrowRight aria-hidden="true" /></button>}</td></tr>; })}</tbody></table></div>
            <div className="mobile-audit-list" aria-label={t("audit.timelineList")}>{filteredEvents.map((event) => { const destination = resourceRoute(event); return <article className="mobile-audit-card" key={event.eventId}><div className="mobile-audit-heading"><span className="audit-event-name"><span className="audit-event-icon"><EventIcon event={event} /></span><span><strong>{actionLabel(event.action, t)}</strong><small>{eventSummary(event, t)}</small></span></span><time dateTime={event.occurredAt}>{formatDate(event.occurredAt, locale)}</time></div><dl className="mobile-audit-details"><div><dt>{t("audit.actor")}</dt><dd>{shortId(event.actorId, t)}</dd></div><div><dt>{t("audit.resource")}</dt><dd><code>{event.resourceType} · {shortId(event.resourceId, t)}</code></dd></div><div><dt>{t("audit.trace")}</dt><dd>{shortId(event.requestId, t)} / {shortId(event.correlationId, t)}</dd></div></dl>{destination && <button className="table-action" type="button" onClick={() => navigate(destination)}>{t("audit.openResource")} <ArrowRight aria-hidden="true" /></button>}</article>; })}</div>
            {audit.hasNextPage && <div className="audit-load-more"><button className="secondary-button" type="button" onClick={() => void audit.fetchNextPage()} disabled={audit.isFetchingNextPage}>{audit.isFetchingNextPage ? t("audit.loadingOlder") : t("audit.loadOlder")}</button></div>}
          </>
        )}
      </section>
    </>
  );
}
