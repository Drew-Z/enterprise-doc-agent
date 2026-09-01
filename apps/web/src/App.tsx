import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  ChevronDown,
  Files,
  LayoutDashboard,
  LogOut,
  Menu,
  Search,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Upload,
  X,
  KeyRound,
} from "lucide-react";

import { fetchReadiness } from "./api/health";
import { formatApiError } from "./api/errorDisplay";
import { AgentWorkspace } from "./agent";
import { DocumentsPage } from "./product/DocumentsPage";
import { AuditPage } from "./product/AuditPage";
import { RuntimeOverview } from "./product/RuntimeOverview";
import { IdentityPage } from "./product/IdentityPage";
import { fetchProductSession, logoutProductSession, ProductSessionApiError, type ProductSession } from "./product/sessionApi";
import { useProductRoute, type ProductRoute } from "./product/routes";
import { showcaseAgentWorkspaceDependencies } from "./product/showcaseAgentClient";
import { showcaseReadiness, showcaseRunId } from "./product/showcaseData";
import { isShowcaseMode } from "./product/showcase";
import { createUploadTokenStore } from "./upload/persistence";
import { setLocale, useLocale, useT } from "./i18n";
import "./styles.css";
import "./product/product.css";

const workflowSteps = [
  { label: "Ingest", detail: "Upload and index governed content", icon: Upload },
  { label: "Reason", detail: "Generate answers from authorized evidence", icon: Sparkles },
  { label: "Review", detail: "Approve controlled publication", icon: ShieldCheck },
] as const;

const navigationItems: Array<{ route: ProductRoute; icon: typeof Files }> = [
  { route: "overview", icon: LayoutDashboard },
  { route: "documents", icon: Files },
  { route: "agent-runs", icon: Sparkles },
  { route: "audit", icon: ShieldCheck },
  { route: "identity", icon: KeyRound },
];

const showcaseSession: ProductSession = {
  tenantId: "00000000-0000-4000-8000-000000000001",
  actorId: "00000000-0000-4000-8000-000000000002",
  role: "owner",
  capabilities: {
    documentRead: true,
    documentWrite: false,
    agentRunCreate: false,
    auditRead: true,
    auditExport: false,
    approvalDecide: false,
  },
};

function shortTenantId(tenantId: string): string {
  return tenantId.slice(0, 8);
}

export function App() {
  const [route, navigate] = useProductRoute();
  const t = useT();
  const locale = useLocale();
  const showcaseMode = isShowcaseMode();
  const tokenStore = useMemo(() => createUploadTokenStore(sessionStorage), []);
  const [authRevision, setAuthRevision] = useState(0);
  const [isCommandOpen, setIsCommandOpen] = useState(false);
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);
  const [commandQuery, setCommandQuery] = useState("");
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [logoutUnconfirmed, setLogoutUnconfirmed] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const readiness = useQuery({
    queryKey: ["platform-readiness", showcaseMode ? "showcase" : "live"],
    queryFn: showcaseMode ? () => Promise.resolve(showcaseReadiness) : fetchReadiness,
    retry: false,
    staleTime: showcaseMode ? Infinity : 5_000,
    refetchInterval: showcaseMode ? false : 15_000,
  });
  const hasToken = tokenStore.load() !== null;
  const session = useQuery({
    queryKey: ["product-session", showcaseMode ? "showcase" : authRevision],
    queryFn: ({ signal }) => showcaseMode
      ? Promise.resolve(showcaseSession)
      : fetchProductSession(tokenStore.load() ?? "", signal),
    enabled: showcaseMode || hasToken,
    retry: false,
    staleTime: showcaseMode ? Infinity : 30_000,
  });
  const currentSession = session.data;
  const refreshSession = () => setAuthRevision((current) => current + 1);

  useEffect(() => {
    if (showcaseMode || !hasToken || !(session.error instanceof ProductSessionApiError) || session.error.status !== 401) {
      return;
    }
    tokenStore.clear();
    setSessionExpired(true);
    setAuthRevision((current) => current + 1);
  }, [hasToken, session.error, showcaseMode, tokenStore]);

  useEffect(() => {
    if (session.data !== undefined) setSessionExpired(false);
  }, [session.data]);

  const handleLogout = async () => {
    const token = tokenStore.load();
    if (!token || isLoggingOut || showcaseMode) return;
    setIsLoggingOut(true);
    setLogoutUnconfirmed(false);
    setSessionExpired(false);
    try {
      await logoutProductSession(token);
    } catch {
      // Clearing the browser session remains useful when the API is unavailable,
      // but the user must know that server-side revocation was not confirmed.
      setLogoutUnconfirmed(true);
    } finally {
      tokenStore.clear();
      setIsLoggingOut(false);
      refreshSession();
    }
  };

  const isLoading = readiness.isPending;
  const isUnreachable = readiness.isError;
  const isHealthy = readiness.data?.status === "ready";
  const isDegraded = readiness.data?.status === "not_ready";
  const runtimeLabel = showcaseMode ? t("runtime.showcase") : isLoading ? t("runtime.checking") : isUnreachable ? t("runtime.offline") : isHealthy ? t("runtime.live") : t("runtime.attention");
  const goTo = (nextRoute: ProductRoute) => {
    navigate(nextRoute);
    setIsMobileNavOpen(false);
  };

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandQuery("");
        setIsCommandOpen(true);
      }
      if (event.key === "Escape") {
        setIsCommandOpen(false);
        setIsMobileNavOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  useEffect(() => {
    document.body.classList.toggle("overlay-open", isCommandOpen || isMobileNavOpen);
    return () => document.body.classList.remove("overlay-open");
  }, [isCommandOpen, isMobileNavOpen]);

  const commandActions = showcaseMode
    ? [
        {
          label: t("command.reviewInventory"),
          detail: t("documents.showcaseDetail"),
          icon: Files,
          run: () => goTo("documents"),
        },
        {
          label: t("command.inspectRun"),
          detail: t("overview.reasonDetail"),
          icon: Sparkles,
          run: () => goTo("agent-runs"),
        },
        {
          label: t("audit.title"),
          detail: t("audit.summary"),
          icon: ShieldCheck,
          run: () => goTo("audit"),
        },
        {
          label: t("command.reviewReadiness"),
          detail: t("runtime.summary"),
          icon: Settings2,
          run: () => goTo("runtime"),
        },
      ]
    : [
        {
          label: t("command.reviewDocuments"),
          detail: t("documents.summary"),
          icon: Files,
          run: () => goTo("documents"),
        },
        {
          label: t("command.startRun"),
          detail: t("overview.reasonDetail"),
          icon: Sparkles,
          run: () => goTo("agent-runs"),
        },
        {
          label: t("command.reviewAudit"),
          detail: t("audit.summary"),
          icon: ShieldCheck,
          run: () => goTo("audit"),
        },
        {
          label: t("command.reviewReadiness"),
          detail: t("runtime.summary"),
          icon: Settings2,
          run: () => goTo("runtime"),
        },
      ];
  const filteredCommandActions = commandActions.filter((action) =>
    `${action.label} ${action.detail}`.toLowerCase().includes(commandQuery.trim().toLowerCase()),
  );
  const runCommandAction = (action: (typeof commandActions)[number]) => {
    action.run();
    setIsCommandOpen(false);
    setCommandQuery("");
  };

  const navigation = (
    <>
      <div className="sidebar-section-label">{t("nav.workspace")}</div>
      <nav className="primary-nav" aria-label={t("nav.workspace")}>
        {navigationItems.map(({ route: itemRoute, icon: Icon }) => (
          <button
            key={itemRoute}
            type="button"
            className={route === itemRoute ? "primary-nav-item active" : "primary-nav-item"}
            aria-current={route === itemRoute ? "page" : undefined}
            onClick={() => goTo(itemRoute)}
          >
            <Icon aria-hidden="true" />
          {itemRoute === "overview" ? t("nav.overview") : itemRoute === "documents" ? t("nav.documents") : itemRoute === "agent-runs" ? t("nav.agentRuns") : itemRoute === "audit" ? t("nav.audit") : t("nav.identity")}
          </button>
        ))}
      </nav>

      <div className="sidebar-section-label">{t("nav.system")}</div>
      <nav className="primary-nav" aria-label={t("nav.system")}>
        <button
          type="button"
          className={route === "runtime" ? "primary-nav-item active" : "primary-nav-item"}
          aria-current={route === "runtime" ? "page" : undefined}
          onClick={() => goTo("runtime")}
        >
          <Settings2 aria-hidden="true" />
          {t("nav.runtime")}
        </button>
      </nav>
    </>
  );

  return (
    <div className="app-shell">
      <aside className="app-sidebar" aria-label={t("nav.workspace")}>
        <Brand />
        <div className="workspace-switcher" aria-label={t("nav.workspace")}>
          <span><small>{t("nav.workspace")}</small>{t("workspace.operations")}</span>
          <ChevronDown aria-hidden="true" />
        </div>
        {navigation}
        <div className="sidebar-spacer" />
        <RuntimeStatus runtimeLabel={runtimeLabel} isHealthy={isHealthy} isDegraded={isDegraded} showcaseMode={showcaseMode} />
      </aside>

      <div className="app-content">
        <header className="topbar">
          <button className="mobile-menu-button" type="button" aria-label={t("nav.open")} onClick={() => setIsMobileNavOpen(true)}>
            <Menu aria-hidden="true" />
          </button>
          <div className="topbar-brand"><Brand compact /></div>
          <button
            className="command-search"
            type="button"
            aria-label={t("search.open")}
            onClick={() => { setCommandQuery(""); setIsCommandOpen(true); }}
          >
            <Search aria-hidden="true" />
            <span>{t("search.placeholder")}</span>
            <kbd>Ctrl K</kbd>
          </button>
          <div className="topbar-actions">
            {showcaseMode && (
              <span className="showcase-pill">
                <span className="showcase-pill-wide">{t("showcase.pill")}</span>
                <span className="showcase-pill-compact">{t("showcase.pillCompact")}</span>
              </span>
            )}
            <span className="environment-label">{t("session.local")}</span>
            <button className="locale-button" type="button" aria-label={t("locale.label")} onClick={() => setLocale(locale === "zh" ? "en" : "zh")}>
              {t("locale.switch")}
            </button>
            {currentSession !== undefined && (
              <span className="session-identity" title={`${t("session.tenant")} ${currentSession.tenantId}`}>
                <strong>{t("session.tenant")} {shortTenantId(currentSession.tenantId)}</strong>
                <small>{currentSession.role === "owner" ? t("session.owner") : t("session.member")}</small>
              </span>
            )}
            {session.isError && hasToken && !showcaseMode && <span className="session-error" title={formatApiError(session.error, t("session.unavailable"), t("common.requestId"))}>{t("session.unavailable")}</span>}
            <span className="avatar-badge" role="img" aria-label={currentSession?.role === "owner" ? t("session.owner") : t("session.local")} title={currentSession?.role === "owner" ? t("session.owner") : t("session.local")}>ZB</span>
            {!showcaseMode && hasToken && (
              <button
                className="session-logout"
                type="button"
                onClick={() => void handleLogout()}
                disabled={isLoggingOut}
                aria-label={t("session.logout")}
                title={t("session.logout")}
              >
                <LogOut aria-hidden="true" />
                <span>{t("session.logout")}</span>
              </button>
            )}
          </div>
        </header>

        <main className="workspace">
          {sessionExpired && (
            <div className="session-warning-banner" role="alert" aria-live="assertive">
              <ShieldAlert aria-hidden="true" />
              <div>
                <strong>{t("session.expired")}</strong>
                <span>{t("session.expiredDetail")}</span>
              </div>
              <button
                className="icon-button"
                type="button"
                aria-label={t("session.dismissExpiredWarning")}
                title={t("session.dismissExpiredWarning")}
                onClick={() => setSessionExpired(false)}
              >
                <X aria-hidden="true" />
              </button>
            </div>
          )}
          {logoutUnconfirmed && (
            <div className="session-warning-banner" role="alert" aria-live="assertive">
              <ShieldAlert aria-hidden="true" />
              <div>
                <strong>{t("session.logoutUnconfirmed")}</strong>
                <span>{t("session.logoutUnconfirmedDetail")}</span>
              </div>
              <button
                className="icon-button"
                type="button"
                aria-label={t("session.dismissLogoutWarning")}
                title={t("session.dismissLogoutWarning")}
                onClick={() => setLogoutUnconfirmed(false)}
              >
                <X aria-hidden="true" />
              </button>
            </div>
          )}
          {showcaseMode && (
            <div className="showcase-banner" role="status" aria-label="Showcase mode">
              <ShieldCheck aria-hidden="true" />
              <div>
                <strong>{t("showcase.title")}</strong>
                <span>{t("showcase.detail")}</span>
              </div>
            </div>
          )}

          {route === "overview" && (
            <>
              <header className="product-page-header overview-header">
                <div>
                  <p className="eyebrow">{t("overview.eyebrow")}</p>
                  <h1>{t("overview.title")}</h1>
                  <p className="page-summary">{t("overview.summary")}</p>
                </div>
                <div className="workspace-summary" aria-label="Workspace summary">
                  <span className={`live-pill ${isHealthy ? "healthy" : isDegraded ? "warning" : ""}`}>
                    <span className="status-dot" />{runtimeLabel}
                  </span>
                  <span className="summary-count">{showcaseMode ? t("overview.servicesSnapshot") : t("overview.servicesMonitored")}</span>
                </div>
              </header>

              <section className="workflow-summary" aria-label={t("overview.workflow")}>
                {workflowSteps.map(({ icon: Icon }, index) => (
                  <button className="workflow-step" type="button" key={index} onClick={() => goTo(index === 0 ? "documents" : "agent-runs")}>
                    <span className="workflow-icon" aria-hidden="true"><Icon /></span>
                    <span className="workflow-copy"><strong>{index === 0 ? t("overview.ingest") : index === 1 ? t("overview.reason") : t("overview.review")}</strong><span>{index === 0 ? t("overview.ingestDetail") : index === 1 ? t("overview.reasonDetail") : t("overview.reviewDetail")}</span></span>
                    {index < workflowSteps.length - 1 && <ArrowRight className="workflow-arrow" aria-hidden="true" />}
                  </button>
                ))}
              </section>

              <div className="overview-actions" aria-label="Primary actions">
                <button type="button" onClick={() => goTo("documents")}>
                  <Files aria-hidden="true" /><span><strong>{t("overview.manage")}</strong><small>{t("overview.manageDetail")}</small></span><ArrowRight aria-hidden="true" />
                </button>
                <button type="button" onClick={() => goTo("agent-runs")}>
                  <Sparkles aria-hidden="true" /><span><strong>{t("overview.run")}</strong><small>{t("overview.runDetail")}</small></span><ArrowRight aria-hidden="true" />
                </button>
              </div>

              <RuntimeOverview readiness={readiness} sourceLabel={showcaseMode ? t("showcase.pill") : undefined} />
            </>
          )}

          {route === "documents" && <DocumentsPage navigate={goTo} showcaseMode={showcaseMode} canWrite={currentSession?.capabilities.documentWrite ?? false} onSessionChange={refreshSession} />}

          {route === "audit" && <AuditPage navigate={goTo} showcaseMode={showcaseMode} canExport={currentSession?.capabilities.auditExport ?? false} canManageGovernance={currentSession?.role === "owner"} />}

          {route === "identity" && <IdentityPage showcaseMode={showcaseMode} canManage={currentSession?.role === "owner"} currentActorId={currentSession?.actorId} />}

          {route === "agent-runs" && (
            <>
              <header className="product-page-header">
                <div><p className="eyebrow">{t("overview.reason")}</p><h1>{t("nav.agentRuns")}</h1><p className="page-summary">{t("overview.reasonDetail")}</p></div>
              </header>
              <AgentWorkspace
                dependencies={showcaseMode ? showcaseAgentWorkspaceDependencies : undefined}
                initialRunId={showcaseMode ? showcaseRunId : undefined}
                readOnly={showcaseMode}
                openDocuments={() => goTo("documents")}
                canCreateRuns={currentSession?.capabilities.agentRunCreate ?? false}
                canDecideApproval={currentSession?.capabilities.approvalDecide ?? false}
              />
            </>
          )}

          {route === "runtime" && (
            <>
              <header className="product-page-header">
                <div><p className="eyebrow">{t("runtime.eyebrow")}</p><h1>{t("runtime.title")}</h1><p className="page-summary">{t("runtime.summary")}</p></div>
              </header>
              <RuntimeOverview readiness={readiness} sourceLabel={showcaseMode ? t("showcase.pill") : undefined} />
            </>
          )}
        </main>
      </div>

      {isMobileNavOpen && (
        <div className="mobile-nav-overlay" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setIsMobileNavOpen(false);
        }}>
          <aside className="mobile-nav-drawer" aria-label={t("nav.mobile")}>
            <div className="mobile-nav-heading"><Brand compact /><button className="icon-button" type="button" aria-label={t("nav.close")} onClick={() => setIsMobileNavOpen(false)}><X aria-hidden="true" /></button></div>
            <div className="workspace-switcher"><span><small>{t("nav.workspace")}</small>{t("workspace.operations")}</span><ChevronDown aria-hidden="true" /></div>
            {navigation}
            <div className="sidebar-spacer" />
            <RuntimeStatus runtimeLabel={runtimeLabel} isHealthy={isHealthy} isDegraded={isDegraded} showcaseMode={showcaseMode} />
          </aside>
        </div>
      )}

      {isCommandOpen && (
        <div className="command-overlay" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setIsCommandOpen(false);
        }}>
          <section className="command-dialog" role="dialog" aria-modal="true" aria-labelledby="command-title">
            <div className="command-dialog-heading">
              <div><p className="eyebrow">{t("search.eyebrow")}</p><h2 id="command-title">{t("search.title")}</h2></div>
              <button className="icon-button" type="button" aria-label={t("common.close")} title={t("common.close")} onClick={() => setIsCommandOpen(false)}><X aria-hidden="true" /></button>
            </div>
            <label className="command-input" htmlFor="workspace-command-input">
              <Search aria-hidden="true" />
              <input
                id="workspace-command-input"
                aria-label={t("search.inputLabel")}
                autoFocus
                value={commandQuery}
                onChange={(event) => setCommandQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && filteredCommandActions[0] !== undefined) runCommandAction(filteredCommandActions[0]);
                }}
                placeholder={t("search.placeholder")}
              />
            </label>
            <div className="command-results" aria-live="polite">
              {filteredCommandActions.length > 0 ? filteredCommandActions.map(({ label, detail, icon: Icon, run }) => (
                <button key={label} className="command-result" type="button" onClick={() => runCommandAction({ label, detail, icon: Icon, run })}>
                  <span className="command-result-icon" aria-hidden="true"><Icon /></span>
                  <span><strong>{label}</strong><small>{detail}</small></span>
                  <ArrowRight aria-hidden="true" />
                </button>
              )) : <p className="command-empty">{t("search.empty")}</p>}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function Brand({ compact = false }: { compact?: boolean }) {
  const t = useT();
  return (
    <div className={compact ? "sidebar-brand compact-brand" : "sidebar-brand"}>
      <span className="brand-mark" aria-hidden="true">ED</span>
      <div><strong>{t("brand.name")}</strong><span>{t("brand.subtitle")}</span></div>
    </div>
  );
}

function RuntimeStatus({
  runtimeLabel,
  isHealthy,
  isDegraded,
  showcaseMode,
}: {
  runtimeLabel: string;
  isHealthy: boolean;
  isDegraded: boolean;
  showcaseMode: boolean;
}) {
  const t = useT();
  return (
    <div className="sidebar-status">
      <span className={`status-dot ${isHealthy ? "healthy" : isDegraded ? "warning" : ""}`} />
      <div><strong>{runtimeLabel}</strong><span>{showcaseMode ? t("runtime.localFixture") : t("runtime.cpu")}</span></div>
    </div>
  );
}
