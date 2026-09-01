import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";
import {
  ArrowRight,
  Building2,
  CircleAlert,
  FileCheck2,
  FileText,
  LockKeyhole,
  LoaderCircle,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";

import type { DocumentInventoryItem } from "../agent/api/schemas";
import { UploadWorkspace } from "../upload/UploadWorkspace";
import { createUploadRecoveryStore, createUploadTokenStore } from "../upload/persistence";
import { DocumentAccessDrawer } from "./DocumentAccessDrawer";
import { fetchDocumentInventory } from "./documentsApi";
import type { ProductRoute } from "./routes";
import { showcaseInventory } from "./showcaseData";
import { useLocale, useT } from "../i18n";
import { formatApiError } from "../api/errorDisplay";

interface DocumentsPageProps {
  navigate: (route: ProductRoute) => void;
  showcaseMode?: boolean;
  canWrite?: boolean;
  onSessionChange?: () => void;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  const units = ["KiB", "MiB", "GiB"];
  let amount = value;
  let unit = "B";
  for (const nextUnit of units) {
    amount /= 1024;
    unit = nextUnit;
    if (amount < 1024) break;
  }
  return `${amount >= 10 ? amount.toFixed(1) : amount.toFixed(2)} ${unit}`;
}

function formatDate(value: string, locale: "en" | "zh"): string {
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function shortVersion(value: string): string {
  return value.slice(0, 8);
}

function formatStage(value: string | null, t: ReturnType<typeof useT>): string {
  if (value === null) return t("documents.stage.awaiting");
  if (value === "parse") return t("documents.stage.parse");
  if (value === "chunk") return t("documents.stage.chunk");
  if (value === "embed") return t("documents.stage.embed");
  return value.replaceAll("_", " ");
}

function displayStatus(document: DocumentInventoryItem): "ready" | "failed" | "uploaded" {
  if (document.versionStatus === "failed" || document.ingestionStatus === "failed") return "failed";
  if (
    document.versionStatus === "ready" &&
    document.ingestionStatus === "succeeded" &&
    document.ingestionStage === "ready"
  ) return "ready";
  return "uploaded";
}

function statusLabel(status: ReturnType<typeof displayStatus>, translate: ReturnType<typeof useT>): string {
  return status === "ready" ? translate("documents.ready") : status === "failed" ? translate("documents.failed") : translate("documents.processing");
}

type InventoryFilter = "all" | ReturnType<typeof displayStatus>;

function DocumentStatus({ document }: { document: DocumentInventoryItem }) {
  const t = useT();
  const status = displayStatus(document);
  return (
    <span className="document-status-cell">
      <span className={`status-badge status-${status}`}>
        <span className={`status-dot ${status === "ready" ? "healthy" : status === "failed" ? "warning" : ""}`} />
        {statusLabel(status, t)}
      </span>
      <small>{document.errorCode ?? formatStage(document.ingestionStage, t)}</small>
    </span>
  );
}

function DocumentAccess({ document }: { document: DocumentInventoryItem }) {
  const t = useT();
  const restricted = document.accessMode === "restricted";
  return (
    <span className={`access-badge ${restricted ? "access-restricted" : "access-tenant"}`}>
      {restricted ? <LockKeyhole aria-hidden="true" /> : <Building2 aria-hidden="true" />}
      {restricted ? t("documents.access.restricted") : t("documents.access.tenant")}
    </span>
  );
}

function describeInventoryError(error: unknown, t: ReturnType<typeof useT>): string {
  return formatApiError(error, t("documents.unavailable"), t("common.requestId"));
}

function hasRecoverableUpload(storage: Storage): boolean {
  try {
    return createUploadRecoveryStore(storage).load() !== null;
  } catch {
    return false;
  }
}

export function DocumentsPage({ navigate, showcaseMode = false, canWrite = true, onSessionChange }: DocumentsPageProps) {
  const t = useT();
  const locale = useLocale();
  const tokenStore = useMemo(() => createUploadTokenStore(sessionStorage), []);
  const [authRevision, setAuthRevision] = useState(0);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<InventoryFilter>("all");
  const [isUploadOpen, setIsUploadOpen] = useState(
    () => !showcaseMode && hasRecoverableUpload(sessionStorage),
  );
  const [accessDocument, setAccessDocument] = useState<DocumentInventoryItem | null>(null);
  const hasToken = showcaseMode || tokenStore.load() !== null;
  const documents = useQuery({
    queryKey: ["document-inventory", showcaseMode ? "showcase" : authRevision],
    queryFn: ({ signal }) => showcaseMode
      ? Promise.resolve([...showcaseInventory])
      : fetchDocumentInventory(tokenStore.load() ?? "", signal),
    enabled: hasToken,
    retry: false,
    staleTime: 10_000,
  });

  const filteredDocuments = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return (documents.data ?? []).filter((document) =>
      (statusFilter === "all" || displayStatus(document) === statusFilter) &&
      (normalized === "" || `${document.filename} ${document.documentId} ${document.versionId}`.toLowerCase().includes(normalized)),
    );
  }, [documents.data, query, statusFilter]);
  const emptyMessage = statusFilter === "all" ? t("documents.noMatch") : t("documents.noFilterMatch");
  const readyAssets = new Set(
    (documents.data ?? [])
      .filter((document) => displayStatus(document) === "ready")
      .map((document) => document.documentId),
  ).size;

  const refreshInventory = useCallback(() => {
    setAuthRevision((current) => current + 1);
  }, []);
  const handleTokenChange = useCallback(() => {
    refreshInventory();
    onSessionChange?.();
  }, [onSessionChange, refreshInventory]);
  const canOpenUpload = !showcaseMode && canWrite;

  return (
    <>
      <header className="product-page-header">
        <div>
          <p className="eyebrow">{t("documents.eyebrow")}</p>
          <h1>{t("documents.title")}</h1>
          <p className="page-summary">{t("documents.summary")}</p>
        </div>
        <button
          className="command-button"
          type="button"
          disabled={showcaseMode || !canWrite}
          title={showcaseMode ? t("showcase.detail") : !canWrite ? t("permissions.writeDenied") : undefined}
          onClick={() => setIsUploadOpen(true)}
        >
          <Upload aria-hidden="true" />
          {showcaseMode ? t("documents.demoOnly") : t("documents.upload")}
        </button>
      </header>

      <div className="inventory-metrics" aria-label="Document inventory summary">
        <div><span>{t("documents.readyAssets")}</span><strong>{hasToken && documents.data ? readyAssets : "-"}</strong></div>
        <div><span>{t("documents.indexedVersions")}</span><strong>{hasToken && documents.data ? documents.data.length : "-"}</strong></div>
        <div><span>{t("documents.accessScope")}</span><strong>{showcaseMode ? t("showcase.pill") : hasToken ? t("documents.currentTenant") : t("documents.notConnected")}</strong></div>
      </div>

      <section className="inventory-section product-section" aria-labelledby="inventory-title">
        <div className="inventory-toolbar">
          <div>
          <h2 id="inventory-title">{t("documents.inventory")}</h2>
            <p>{showcaseMode ? t("documents.showcaseDetail") : t("documents.liveDetail")}</p>
          </div>
          <div className="inventory-toolbar-actions">
              <div className="inventory-filter" role="group" aria-label={t("documents.filterStatus")}>
              {[
                { value: "all" as const, label: t("documents.all") },
                { value: "ready" as const, label: t("documents.ready") },
                { value: "uploaded" as const, label: t("documents.processing") },
                { value: "failed" as const, label: t("documents.failed") },
              ].map((filter) => (
                <button
                  key={filter.value}
                  className={statusFilter === filter.value ? "active" : undefined}
                  type="button"
                  aria-pressed={statusFilter === filter.value}
                  disabled={!hasToken}
                  onClick={() => setStatusFilter(filter.value)}
                >
                  {filter.label}
                </button>
              ))}
            </div>
              <label className="inventory-search" htmlFor="document-search">
              <Search aria-hidden="true" />
              <input
                id="document-search"
                type="search"
                aria-label={t("documents.search")}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("documents.search")}
                disabled={!hasToken}
              />
            </label>
            <button
              className="icon-button"
              type="button"
              aria-label={t("documents.refresh")}
              title={t("documents.refresh")}
              disabled={!hasToken || documents.isFetching}
              onClick={() => void documents.refetch()}
            >
              <RefreshCw aria-hidden="true" className={documents.isFetching ? "spin" : undefined} />
            </button>
          </div>
        </div>

        {!hasToken && (
          <div className="inventory-empty">
            <ShieldCheck aria-hidden="true" />
            <div>
              <h3>{t("documents.connect")}</h3>
              <p>{t("documents.authRequired")}</p>
            </div>
            <button className="secondary-button" type="button" onClick={() => setIsUploadOpen(true)}>
              {t("documents.openAccess")}
            </button>
          </div>
        )}

        {hasToken && documents.isPending && (
          <div className="inventory-empty" role="status">
            <LoaderCircle className="spin" aria-hidden="true" />
            <div><h3>{t("documents.loading")}</h3><p>{t("documents.loadingDetail")}</p></div>
          </div>
        )}

        {hasToken && documents.isError && (
          <div className="inventory-empty inventory-error" role="alert">
            <CircleAlert aria-hidden="true" />
            <div><h3>{t("documents.unavailable")}</h3><p>{describeInventoryError(documents.error, t)}</p></div>
            <button className="secondary-button" type="button" onClick={() => void documents.refetch()}>{t("documents.tryAgain")}</button>
          </div>
        )}

        {hasToken && documents.isSuccess && documents.data.length === 0 && (
          <div className="inventory-empty">
            <FileText aria-hidden="true" />
            <div><h3>{t("documents.emptyTitle")}</h3><p>{t("documents.emptyDetail")}</p></div>
            <button className="secondary-button" type="button" disabled={!canOpenUpload} title={!canOpenUpload ? t("permissions.writeDenied") : undefined} onClick={() => setIsUploadOpen(true)}>{t("documents.upload")}</button>
          </div>
        )}

        {hasToken && documents.isSuccess && documents.data.length > 0 && (
          <>
            <div className="document-table-wrap">
              <table className="document-table">
              <thead>
                <tr><th>{t("documents.document")}</th><th>{t("documents.status")}</th><th>{t("documents.access.mode")}</th><th>{t("documents.version")}</th><th>{t("documents.updated")}</th><th>{t("documents.size")}</th><th><span className="sr-only">{t("documents.actions")}</span></th></tr>
              </thead>
              <tbody>
                {filteredDocuments.map((document: DocumentInventoryItem) => {
                  return (
                  <tr key={document.versionId}>
                    <td>
                      <span className="document-name"><FileCheck2 aria-hidden="true" /><span><strong>{document.filename}</strong><small>{document.documentId}</small></span></span>
                    </td>
                    <td><DocumentStatus document={document} /></td>
                    <td><DocumentAccess document={document} /></td>
                    <td><code>v{document.versionNumber} · {shortVersion(document.versionId)}</code></td>
                    <td>{formatDate(document.updatedAt, locale)}</td>
                    <td>{formatBytes(document.sizeBytes)}</td>
                    <td>
                      <span className="document-actions">
                        {document.canManage && !showcaseMode && (
                          <button className="icon-button" type="button" aria-label={t("documents.access.manage")} title={t("documents.access.manage")} onClick={() => setAccessDocument(document)}>
                            <Settings2 aria-hidden="true" />
                          </button>
                        )}
                        <button className="table-action" type="button" onClick={() => navigate("agent-runs")}>
                          {t("documents.useAgent")} <ArrowRight aria-hidden="true" />
                        </button>
                      </span>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
              </table>
              {filteredDocuments.length === 0 && <p className="table-empty">{emptyMessage}</p>}
            </div>
            <div className="mobile-document-list" aria-label={t("documents.inventoryList")}>
              {filteredDocuments.map((document: DocumentInventoryItem) => (
                <article className="mobile-document-card" key={document.versionId}>
                  <div className="mobile-document-card-heading">
                    <span className="document-name"><FileCheck2 aria-hidden="true" /><span><strong>{document.filename}</strong><small>{document.documentId}</small></span></span>
                    <DocumentStatus document={document} />
                  </div>
                  <dl className="mobile-document-details">
                    <div><dt>{t("documents.version")}</dt><dd><code>v{document.versionNumber} · {shortVersion(document.versionId)}</code></dd></div>
                    <div><dt>{t("documents.updated")}</dt><dd>{formatDate(document.updatedAt, locale)}</dd></div>
                    <div><dt>{t("documents.size")}</dt><dd>{formatBytes(document.sizeBytes)}</dd></div>
                    <div><dt>{t("documents.access.mode")}</dt><dd><DocumentAccess document={document} /></dd></div>
                  </dl>
                  <div className="mobile-document-actions">
                    {document.canManage && !showcaseMode && (
                      <button className="icon-button" type="button" aria-label={t("documents.access.manage")} title={t("documents.access.manage")} onClick={() => setAccessDocument(document)}>
                        <Settings2 aria-hidden="true" />
                      </button>
                    )}
                    <button className="table-action" type="button" onClick={() => navigate("agent-runs")}>
                      {t("documents.useAgent")} <ArrowRight aria-hidden="true" />
                    </button>
                  </div>
                </article>
              ))}
              {filteredDocuments.length === 0 && <p className="table-empty">{emptyMessage}</p>}
            </div>
          </>
        )}
      </section>

      {isUploadOpen && (
        <div className="product-drawer-overlay" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setIsUploadOpen(false);
        }}>
          <section className="product-drawer" role="dialog" aria-modal="true" aria-label={t("documents.upload")}>
            <div className="product-drawer-heading">
              <div><p className="eyebrow">{t("documents.localDevelopment")}</p><h2>{t("documents.openUpload")}</h2></div>
              <button className="icon-button" type="button" aria-label={t("documents.closeUpload")} title={t("documents.closeUpload")} onClick={() => setIsUploadOpen(false)}><X aria-hidden="true" /></button>
            </div>
            <UploadWorkspace canUpload={canWrite} onTokenChange={handleTokenChange} onCompleted={refreshInventory} />
          </section>
        </div>
      )}

      {accessDocument !== null && !showcaseMode && tokenStore.load() !== null && (
        <DocumentAccessDrawer
          document={accessDocument}
          token={tokenStore.load() ?? ""}
          onClose={() => setAccessDocument(null)}
          onUpdated={() => void documents.refetch()}
        />
      )}
    </>
  );
}
