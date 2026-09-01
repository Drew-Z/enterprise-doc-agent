import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, CircleAlert, Clock3, Download, Fingerprint, Gavel, LoaderCircle, Plus, RefreshCw, ShieldCheck, Trash2, CheckCircle2 } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { createUploadTokenStore } from "../upload/persistence";
import { formatApiError } from "../api/errorDisplay";
import { useLocale, useT } from "../i18n";
import {
  createAuditLegalHold,
  fetchAuditLegalHolds,
  fetchAuditRetentionPolicy,
  fetchAuditRetentionPreview,
  fetchAuditRetentionPlan,
  archiveAuditRetentionPlan,
  fetchAuditArchiveBatches,
  fetchAuditArchiveDownload,
  verifyAuditArchiveBatch,
  releaseAuditLegalHold,
  updateAuditRetentionPolicy,
  type AuditLegalHold,
} from "./auditGovernanceApi";
import { showcaseAuditArchiveBatches, showcaseAuditLegalHolds, showcaseAuditRetentionPlan, showcaseAuditRetentionPolicy, showcaseAuditRetentionPreview } from "./auditData";

interface AuditGovernancePanelProps {
  showcaseMode?: boolean;
  canManage?: boolean;
}

function formatDate(value: string | null | undefined, locale: "en" | "zh", fallback: string): string {
  if (!value) return fallback;
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function holdScope(hold: AuditLegalHold, t: ReturnType<typeof useT>): string {
  if (!hold.resourceType || !hold.resourceId) return t("audit.governance.tenantScope");
  return `${hold.resourceType} · ${hold.resourceId.slice(0, 8)}`;
}

function errorText(error: unknown, fallback: string, requestIdLabel: string): string {
  return formatApiError(error, fallback, requestIdLabel);
}

export function AuditGovernancePanel({ showcaseMode = false, canManage = false }: AuditGovernancePanelProps) {
  const t = useT();
  const locale = useLocale();
  const queryClient = useQueryClient();
  const tokenStore = useMemo(() => createUploadTokenStore(sessionStorage), []);
  const token = tokenStore.load() ?? "";
  const enabled = showcaseMode || (token !== "" && canManage);
  const [retentionDays, setRetentionDays] = useState(365);
  const [retentionEnabled, setRetentionEnabled] = useState(false);
  const [holdName, setHoldName] = useState("");
  const [holdReason, setHoldReason] = useState("");
  const [holdResourceType, setHoldResourceType] = useState("");
  const [holdResourceId, setHoldResourceId] = useState("");
  const [holdExpiresAt, setHoldExpiresAt] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const policy = useQuery({
    queryKey: ["audit-retention-policy", showcaseMode ? "showcase" : "live"],
    queryFn: () => showcaseMode ? Promise.resolve(showcaseAuditRetentionPolicy) : fetchAuditRetentionPolicy(token),
    enabled,
    staleTime: 30_000,
  });
  const preview = useQuery({
    queryKey: ["audit-retention-preview", showcaseMode ? "showcase" : "live"],
    queryFn: () => showcaseMode ? Promise.resolve(showcaseAuditRetentionPreview) : fetchAuditRetentionPreview(token),
    enabled,
    staleTime: 15_000,
  });
  const holds = useQuery({
    queryKey: ["audit-legal-holds", showcaseMode ? "showcase" : "live"],
    queryFn: () => showcaseMode ? Promise.resolve(showcaseAuditLegalHolds) : fetchAuditLegalHolds(token),
    enabled,
    staleTime: 15_000,
  });
  const plan = useQuery({
    queryKey: ["audit-retention-plan", showcaseMode ? "showcase" : "live"],
    queryFn: () => showcaseMode ? Promise.resolve(showcaseAuditRetentionPlan) : fetchAuditRetentionPlan(token),
    enabled,
    staleTime: 15_000,
  });
  const archives = useQuery({
    queryKey: ["audit-archive-batches", showcaseMode ? "showcase" : "live"],
    queryFn: () => showcaseMode ? Promise.resolve(showcaseAuditArchiveBatches) : fetchAuditArchiveBatches(token),
    enabled,
    staleTime: 15_000,
  });

  useEffect(() => {
    if (policy.data) {
      setRetentionDays(policy.data.retentionDays);
      setRetentionEnabled(policy.data.isEnabled);
    }
  }, [policy.data]);

  const policyMutation = useMutation({
    mutationFn: () => updateAuditRetentionPolicy(token, { retentionDays, isEnabled: retentionEnabled }),
    onSuccess: (next) => {
      setRetentionDays(next.retentionDays);
      setRetentionEnabled(next.isEnabled);
      void queryClient.invalidateQueries({ queryKey: ["audit-retention-preview"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-retention-plan"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
  });
  const createHoldMutation = useMutation({
    mutationFn: () => createAuditLegalHold(token, {
      name: holdName.trim(),
      reason: holdReason.trim(),
      ...(holdResourceType.trim() ? { resourceType: holdResourceType.trim(), resourceId: holdResourceId.trim() } : {}),
      ...(holdExpiresAt ? { expiresAt: new Date(holdExpiresAt).toISOString() } : {}),
    }),
    onSuccess: () => {
      setHoldName(""); setHoldReason(""); setHoldResourceType(""); setHoldResourceId(""); setHoldExpiresAt(""); setFormError(null);
      void queryClient.invalidateQueries({ queryKey: ["audit-legal-holds"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-retention-preview"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-retention-plan"] });
    },
  });
  const releaseMutation = useMutation({
    mutationFn: (holdId: string) => releaseAuditLegalHold(token, holdId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["audit-legal-holds"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-retention-preview"] });
    },
  });
  const archiveMutation = useMutation({
    mutationFn: () => archiveAuditRetentionPlan(token),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["audit-archive-batches"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-retention-plan"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-retention-preview"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
  });
  const verifyMutation = useMutation({
    mutationFn: (batchId: string) => verifyAuditArchiveBatch(token, batchId),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["audit-archive-batches"] });
      if (!result.valid) setFormError(t("audit.governance.verifyFailed"));
    },
  });
  const downloadMutation = useMutation({
    mutationFn: (batchId: string) => fetchAuditArchiveDownload(token, batchId),
  });

  function submitHold(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    if (!holdName.trim() || !holdReason.trim()) {
      setFormError(t("audit.governance.holdRequired"));
      return;
    }
    if (holdResourceType.trim() !== "" && !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(holdResourceId.trim())) {
      setFormError(t("audit.governance.resourceIdInvalid"));
      return;
    }
    createHoldMutation.mutate();
  }

  const displayHolds = holds.data ?? [];
  const notOwnerMessage = !showcaseMode && !canManage ? t("audit.governance.ownerOnly") : null;
  const loading = policy.isPending || preview.isPending || holds.isPending || plan.isPending || archives.isPending;
  const queryError = policy.error ?? preview.error ?? holds.error ?? plan.error ?? archives.error;

  return (
    <section className="audit-governance product-section" aria-labelledby="audit-governance-title">
      <div className="audit-governance-header">
        <div><p className="eyebrow">{t("audit.governance.eyebrow")}</p><h2 id="audit-governance-title">{t("audit.governance.title")}</h2><p>{t("audit.governance.summary")}</p></div>
        <div className="audit-governance-badge"><Gavel aria-hidden="true" />{showcaseMode ? t("audit.governance.snapshot") : canManage ? t("audit.governance.ownerMode") : t("audit.governance.readOnly")}</div>
      </div>
      {notOwnerMessage && <p className="permission-notice"><ShieldCheck aria-hidden="true" />{notOwnerMessage}</p>}
      {queryError && <div className="audit-governance-error" role="alert"><CircleAlert aria-hidden="true" />{errorText(queryError, t("audit.governance.loadError"), t("common.requestId"))}<button className="icon-button" type="button" aria-label={t("audit.refresh")} onClick={() => { void policy.refetch(); void preview.refetch(); void holds.refetch(); void plan.refetch(); void archives.refetch(); }}><RefreshCw aria-hidden="true" /></button></div>}
      {loading && <div className="audit-governance-loading" role="status"><LoaderCircle className="spin" aria-hidden="true" />{t("audit.governance.loading")}</div>}
      {!loading && !queryError && enabled && (
        <>
          <div className="audit-governance-grid">
            <div className="audit-governance-card">
              <div className="audit-governance-card-heading"><div><h3>{t("audit.governance.retentionTitle")}</h3><p>{t("audit.governance.retentionDetail")}</p></div><Clock3 aria-hidden="true" /></div>
              <div className="governance-retention-controls">
                <label className="access-field"><span>{t("audit.governance.retentionDays")}</span><input type="number" min={30} max={3650} value={retentionDays} disabled={showcaseMode || !canManage || policyMutation.isPending} onChange={(event) => setRetentionDays(Number(event.target.value))} /></label>
                <label className="governance-toggle"><input type="checkbox" checked={retentionEnabled} disabled={showcaseMode || !canManage || policyMutation.isPending} onChange={(event) => setRetentionEnabled(event.target.checked)} /><span>{t("audit.governance.enabled")}</span></label>
              </div>
              <button className="secondary-button" type="button" disabled={showcaseMode || !canManage || policyMutation.isPending || retentionDays < 30 || retentionDays > 3650} onClick={() => policyMutation.mutate()}>{policyMutation.isPending ? t("audit.governance.saving") : t("audit.governance.save")}</button>
              {policyMutation.error && <p className="governance-inline-error" role="alert">{errorText(policyMutation.error, t("audit.governance.saveError"), t("common.requestId"))}</p>}
            </div>
            <div className="audit-governance-card audit-governance-preview">
              <div className="audit-governance-card-heading"><div><h3>{t("audit.governance.previewTitle")}</h3><p>{t("audit.governance.previewDetail")}</p></div><ShieldCheck aria-hidden="true" /></div>
              <div className="governance-preview-metrics"><div><strong>{preview.data?.eligibleEventCount ?? 0}</strong><span>{t("audit.governance.eligible")}</span></div><div><strong>{preview.data?.protectedEventCount ?? 0}</strong><span>{t("audit.governance.protected")}</span></div></div>
              <small>{preview.data?.cutoffAt ? t("audit.governance.cutoff", { value: formatDate(preview.data.cutoffAt, locale, "-") }) : t("audit.governance.previewDisabled")}</small>
            </div>
            <div className="audit-governance-card audit-governance-plan">
              <div className="audit-governance-card-heading"><div><h3>{t("audit.governance.planTitle")}</h3><p>{t("audit.governance.planDetail")}</p></div><Fingerprint aria-hidden="true" /></div>
              <div className="governance-preview-metrics"><div><strong>{plan.data?.eligibleEventCount ?? 0}</strong><span>{t("audit.governance.planCandidates")}</span></div><div><strong>{plan.data?.eligibleEventIds.length ?? 0}</strong><span>{t("audit.governance.planSample")}</span></div></div>
              <small>{plan.data?.fingerprint ? t("audit.governance.planFingerprint", { value: `${plan.data.fingerprint.slice(0, 16)}...` }) : t("audit.governance.planUnavailable")}</small>
              <p className="governance-card-detail">{t("audit.governance.archiveDetail")}</p>
              <button className="secondary-button" type="button" disabled={showcaseMode || !canManage || archiveMutation.isPending || !plan.data?.eligibleEventIds.length} onClick={() => archiveMutation.mutate()}><Archive aria-hidden="true" />{archiveMutation.isPending ? t("audit.governance.archiving") : t("audit.governance.archive")}</button>
              {archiveMutation.data && <p className="governance-inline-success" role="status">{t("audit.governance.archiveSuccess", { value: String(archiveMutation.data.archivedEventCount), fingerprint: `${archiveMutation.data.contentSha256.slice(0, 12)}...` })}</p>}
              {archiveMutation.error && <p className="governance-inline-error" role="alert">{errorText(archiveMutation.error, t("audit.governance.archiveError"), t("common.requestId"))}</p>}
              <div className="governance-archive-list">
                <div className="audit-governance-subheading"><div><h4>{t("audit.governance.recentArchives")}</h4><p>{t("audit.governance.recentArchivesDetail")}</p></div><span className="status-badge">{archives.data?.length ?? 0}</span></div>
                {(archives.data ?? []).length === 0 ? <p className="governance-empty">{t("audit.governance.noArchives")}</p> : (archives.data ?? []).map((batch) => <article className="governance-archive-row" key={batch.batchId}><div><strong>{batch.archivedEventCount} {t("audit.governance.events")}</strong><small>{formatDate(batch.createdAt, locale, "-")} · {batch.contentSha256.slice(0, 12)}...</small></div><div className="governance-archive-actions"><button className="table-action" type="button" disabled={showcaseMode || !canManage || verifyMutation.isPending} onClick={() => verifyMutation.mutate(batch.batchId)}><CheckCircle2 aria-hidden="true" />{verifyMutation.isPending ? t("audit.governance.verifying") : t("audit.governance.verify")}</button><button className="table-action" type="button" disabled={showcaseMode || !canManage || downloadMutation.isPending} onClick={() => downloadMutation.mutate(batch.batchId)}><Download aria-hidden="true" />{downloadMutation.isPending ? t("audit.governance.preparingDownload") : t("audit.governance.download")}</button>{downloadMutation.data?.batchId === batch.batchId && <a className="table-action" href={downloadMutation.data.url} target="_blank" rel="noreferrer"><Download aria-hidden="true" />{t("audit.governance.openDownload")}</a>}</div></article>)}
              </div>
              {verifyMutation.data && <p className={verifyMutation.data.valid ? "governance-inline-success" : "governance-inline-error"} role="status">{verifyMutation.data.valid ? t("audit.governance.verifySuccess") : t("audit.governance.verifyFailed")}</p>}
              {verifyMutation.error && <p className="governance-inline-error" role="alert">{errorText(verifyMutation.error, t("audit.governance.verifyError"), t("common.requestId"))}</p>}
              {downloadMutation.error && <p className="governance-inline-error" role="alert">{errorText(downloadMutation.error, t("audit.governance.downloadError"), t("common.requestId"))}</p>}
            </div>
          </div>
          <div className="audit-governance-holds">
            <div className="audit-governance-subheading"><div><h3>{t("audit.governance.holdsTitle")}</h3><p>{t("audit.governance.holdsDetail")}</p></div><span className="status-badge status-ready">{displayHolds.filter((hold) => !hold.releasedAt).length} {t("audit.governance.active")}</span></div>
            <form className="governance-hold-form" onSubmit={submitHold}>
              <label className="access-field"><span>{t("audit.governance.holdName")}</span><input value={holdName} onChange={(event) => setHoldName(event.target.value)} disabled={showcaseMode || !canManage || createHoldMutation.isPending} maxLength={200} /></label>
              <label className="access-field governance-hold-reason"><span>{t("audit.governance.holdReason")}</span><input value={holdReason} onChange={(event) => setHoldReason(event.target.value)} disabled={showcaseMode || !canManage || createHoldMutation.isPending} maxLength={2000} /></label>
              <label className="access-field"><span>{t("audit.governance.resourceType")}</span><input value={holdResourceType} onChange={(event) => setHoldResourceType(event.target.value)} disabled={showcaseMode || !canManage || createHoldMutation.isPending} placeholder={t("audit.governance.tenantScope")} /></label>
              <label className="access-field"><span>{t("audit.governance.resourceId")}</span><input value={holdResourceId} onChange={(event) => setHoldResourceId(event.target.value)} disabled={showcaseMode || !canManage || createHoldMutation.isPending || !holdResourceType} placeholder="UUID" /></label>
              <label className="access-field"><span>{t("audit.governance.expiresAt")}</span><input type="datetime-local" value={holdExpiresAt} onChange={(event) => setHoldExpiresAt(event.target.value)} disabled={showcaseMode || !canManage || createHoldMutation.isPending} /></label>
              <button className="secondary-button governance-hold-submit" type="submit" disabled={showcaseMode || !canManage || createHoldMutation.isPending}><Plus aria-hidden="true" />{createHoldMutation.isPending ? t("audit.governance.creating") : t("audit.governance.createHold")}</button>
            </form>
            {formError && <p className="governance-inline-error" role="alert">{formError}</p>}
            {createHoldMutation.error && <p className="governance-inline-error" role="alert">{errorText(createHoldMutation.error, t("audit.governance.saveError"), t("common.requestId"))}</p>}
            {displayHolds.length === 0 ? <p className="governance-empty">{t("audit.governance.noHolds")}</p> : <div className="governance-hold-list">{displayHolds.map((hold) => <article className={hold.releasedAt ? "governance-hold released" : "governance-hold"} key={hold.holdId}><div className="governance-hold-main"><strong>{hold.name}</strong><span>{hold.reason}</span><small>{holdScope(hold, t)} · {t("audit.governance.started", { value: formatDate(hold.startsAt, locale, "-") })}{hold.expiresAt ? ` · ${t("audit.governance.expiresShort", { value: formatDate(hold.expiresAt, locale, "-") })}` : ""}</small></div><div className="governance-hold-action">{hold.releasedAt ? <span className="status-badge">{t("audit.governance.released")}</span> : <button className="table-action danger-icon-button" type="button" disabled={showcaseMode || !canManage || releaseMutation.isPending} onClick={() => releaseMutation.mutate(hold.holdId)}><Trash2 aria-hidden="true" />{t("audit.governance.release")}</button>}</div></article>)}</div>}
            {releaseMutation.error && <p className="governance-inline-error" role="alert">{errorText(releaseMutation.error, t("audit.governance.saveError"), t("common.requestId"))}</p>}
          </div>
        </>
      )}
      {!enabled && !notOwnerMessage && <div className="governance-empty"><ShieldCheck aria-hidden="true" />{t("documents.authRequired")}</div>}
    </section>
  );
}
