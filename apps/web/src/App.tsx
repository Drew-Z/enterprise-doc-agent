import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  CircleX,
  Database,
  HardDrive,
  LoaderCircle,
  Radio,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";

import { fetchReadiness, type ComponentName, type ComponentStatus } from "./api/health";
import "./styles.css";

const components: Array<{
  name: ComponentName;
  label: string;
  detail: string;
  icon: typeof Database;
}> = [
  { name: "database", label: "PostgreSQL", detail: "Business state", icon: Database },
  { name: "redis", label: "Redis", detail: "Coordination", icon: Radio },
  { name: "object_store", label: "MinIO", detail: "Object storage", icon: HardDrive },
];

const componentCopy: Record<ComponentStatus, string> = {
  up: "Operational",
  down: "Unavailable",
  timeout: "Timed out",
};

export function App() {
  const readiness = useQuery({
    queryKey: ["platform-readiness"],
    queryFn: fetchReadiness,
    retry: false,
    staleTime: 5_000,
    refetchInterval: 15_000,
  });

  const isLoading = readiness.isPending;
  const isUnreachable = readiness.isError;
  const isHealthy = readiness.data?.status === "ready";
  const isDegraded = readiness.data?.status === "not_ready";

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <span className="brand-mark" aria-hidden="true">
            ED
          </span>
          <div>
            <strong>Enterprise Document Agent</strong>
            <span>Foundation operations</span>
          </div>
        </div>
        <span className="environment-label">Local</span>
      </header>

      <main className="workspace">
        <section className="page-heading" aria-labelledby="overview-title">
          <div>
            <p className="eyebrow">Runtime overview</p>
            <h1 id="overview-title">Platform readiness</h1>
          </div>
          <button
            className="icon-button"
            type="button"
            aria-label="Refresh readiness"
            title="Refresh readiness"
            disabled={isLoading || readiness.isFetching}
            onClick={() => void readiness.refetch()}
          >
            <RefreshCw aria-hidden="true" className={readiness.isFetching ? "spin" : undefined} />
          </button>
        </section>

        <section className="status-band" aria-live="polite">
          {isLoading && (
            <div className="overall-state loading-state" role="status">
              <LoaderCircle aria-hidden="true" className="spin" />
              <div>
                <h2>Checking platform readiness</h2>
                <p>Contacting the API and required local services.</p>
              </div>
            </div>
          )}
          {isHealthy && (
            <div className="overall-state healthy-state">
              <CheckCircle2 aria-hidden="true" />
              <div>
                <h2>Platform ready</h2>
                <p>All required foundation services are accepting work.</p>
              </div>
            </div>
          )}
          {isDegraded && (
            <div className="overall-state degraded-state">
              <TriangleAlert aria-hidden="true" />
              <div>
                <h2>Platform degraded</h2>
                <p>One or more required services are unavailable or timed out.</p>
              </div>
            </div>
          )}
          {isUnreachable && (
            <div className="overall-state unreachable-state" role="alert">
              <CircleX aria-hidden="true" />
              <div>
                <h2>API unreachable</h2>
                <p>The readiness endpoint could not be reached or returned an invalid response.</p>
              </div>
            </div>
          )}
        </section>

        <section className="component-section" aria-labelledby="components-title">
          <div className="section-heading">
            <h2 id="components-title">Required services</h2>
            <span>{readiness.data ? "Live API result" : "Awaiting API result"}</span>
          </div>
          <div className="component-grid">
            {components.map((component) => {
              const status = readiness.data?.checks[component.name].status;
              const Icon = component.icon;
              return (
                <article className="component-card" key={component.name}>
                  <div className="component-icon">
                    <Icon aria-hidden="true" />
                  </div>
                  <div className="component-copy">
                    <h3>{component.label}</h3>
                    <p>{component.detail}</p>
                  </div>
                  <span className={`component-status ${status ?? "unknown"}`}>
                    {status ? componentCopy[status] : "Unknown"}
                  </span>
                </article>
              );
            })}
          </div>
        </section>
      </main>
    </div>
  );
}
