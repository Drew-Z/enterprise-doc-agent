import type { UseQueryResult } from "@tanstack/react-query";
import {
  CheckCircle2,
  CircleX,
  Database,
  Gauge,
  HardDrive,
  LoaderCircle,
  Radio,
  RefreshCw,
  ServerCog,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";

import type { ComponentName, ComponentStatus, ReadinessResponse } from "../api/health";
import { useT } from "../i18n";

const components: Array<{
  name: ComponentName;
  label: string;
  detail: string;
  icon: typeof Database;
}> = [
  { name: "database", label: "PostgreSQL", detail: "runtime.businessState", icon: Database },
  { name: "redis", label: "Redis", detail: "runtime.coordination", icon: Radio },
  { name: "object_store", label: "MinIO", detail: "runtime.objectStorage", icon: HardDrive },
];

const componentCopy: Record<ComponentStatus, string> = {
  up: "runtime.operational",
  down: "runtime.unavailable",
  timeout: "runtime.timedOut",
};

const scopeItems = [
  { key: "staging", status: "verified", title: "runtime.scopeStagingTitle", detail: "runtime.scopeStagingDetail", icon: ShieldCheck },
  { key: "provider", status: "gated", title: "runtime.scopeProviderTitle", detail: "runtime.scopeProviderDetail", icon: Gauge },
  { key: "recovery", status: "deferred", title: "runtime.scopeRecoveryTitle", detail: "runtime.scopeRecoveryDetail", icon: ServerCog },
] as const;

interface RuntimeOverviewProps {
  readiness: UseQueryResult<ReadinessResponse, Error>;
  showHeading?: boolean;
  sourceLabel?: string;
}

export function RuntimeOverview({ readiness, showHeading = true, sourceLabel }: RuntimeOverviewProps) {
  const t = useT();
  const isSnapshot = sourceLabel !== undefined;
  const isLoading = readiness.isPending;
  const isUnreachable = readiness.isError;
  const isHealthy = readiness.data?.status === "ready";
  const isDegraded = readiness.data?.status === "not_ready";
  const checkedAt = readiness.data?.checkedAt !== undefined
    ? Date.parse(readiness.data.checkedAt)
    : readiness.dataUpdatedAt;
  const lastChecked = checkedAt > 0
    ? new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(
        new Date(checkedAt),
      )
    : null;

  return (
    <section className="readiness-section product-section" aria-labelledby="runtime-title">
      {showHeading && (
        <div className="page-heading">
          <div>
            <p className="eyebrow">{t("runtime.overview")}</p>
            <h2 id="runtime-title">{t("runtime.readiness")}</h2>
          </div>
          <div className="runtime-heading-actions">
            {isSnapshot ? <span className="last-checked">{t("runtime.localFixtureLabel")}</span> : lastChecked !== null && <span className="last-checked">{t("runtime.checked", { value: lastChecked })}</span>}
            <button
              className="icon-button"
              type="button"
              aria-label={t("runtime.refresh")}
              title={t("runtime.refresh")}
              disabled={isLoading || readiness.isFetching}
              onClick={() => void readiness.refetch()}
            >
              <RefreshCw aria-hidden="true" className={readiness.isFetching ? "spin" : undefined} />
            </button>
          </div>
        </div>
      )}

      <div className="status-band" aria-live="polite">
        {isLoading && (
          <div className="overall-state loading-state" role="status">
            <LoaderCircle aria-hidden="true" className="spin" />
            <div>
              <h2>{t("runtime.checkingTitle")}</h2>
              <p>{t("runtime.checkingDetail")}</p>
            </div>
          </div>
        )}
        {isHealthy && (
          <div className="overall-state healthy-state">
            <CheckCircle2 aria-hidden="true" />
            <div>
              <h2>{t("runtime.readyTitle")}</h2>
              <p>{t("runtime.readyDetail")}</p>
            </div>
          </div>
        )}
        {isDegraded && (
          <div className="overall-state degraded-state">
            <TriangleAlert aria-hidden="true" />
            <div>
              <h2>{t("runtime.degradedTitle")}</h2>
              <p>{t("runtime.degradedDetail")}</p>
            </div>
          </div>
        )}
        {isUnreachable && (
          <div className="overall-state unreachable-state" role="alert">
            <CircleX aria-hidden="true" />
            <div>
              <h2>{t("runtime.unreachableTitle")}</h2>
              <p>{t("runtime.unreachableDetail")}</p>
            </div>
          </div>
        )}
      </div>

      <div className="component-section" aria-labelledby="components-title">
        <div className="section-heading">
          <h2 id="components-title">{t("runtime.requiredServices")}</h2>
          <span>{sourceLabel ?? (readiness.data ? t("runtime.liveResult") : isUnreachable ? t("runtime.apiUnavailable") : t("runtime.awaitingResult"))}</span>
        </div>
        <div className="component-grid">
          {components.map((component) => {
            const status = readiness.data?.checks[component.name].status;
            const Icon = component.icon;
            return (
              <article className="component-card" key={component.name}>
                <div className="component-icon"><Icon aria-hidden="true" /></div>
                <div className="component-copy">
                  <h3>{component.label}</h3>
                  <p>{t(component.detail as "runtime.businessState" | "runtime.coordination" | "runtime.objectStorage")}</p>
                </div>
                <span className={`component-status ${status ?? "unknown"}`}>
                  {status ? t(componentCopy[status] as "runtime.operational" | "runtime.unavailable" | "runtime.timedOut") : t("runtime.unknown")}
                </span>
              </article>
            );
          })}
        </div>
      </div>

      <div className="runtime-scope" aria-labelledby="runtime-scope-title">
        <div className="section-heading">
          <h2 id="runtime-scope-title">{t("runtime.scopeTitle")}</h2>
          <span>{t("runtime.scopeSummary")}</span>
        </div>
        <div className="runtime-scope-grid">
          {scopeItems.map(({ key, status, title, detail, icon: Icon }) => (
            <article className={`runtime-scope-card ${status}`} key={key}>
              <div className="runtime-scope-icon"><Icon aria-hidden="true" /></div>
              <div className="runtime-scope-copy">
                <h3>{t(title)}</h3>
                <p>{t(detail)}</p>
              </div>
              <span className="runtime-scope-status">{t(`runtime.scopeStatus.${status}`)}</span>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
