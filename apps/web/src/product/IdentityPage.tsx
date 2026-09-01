import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, KeyRound, Link2, LoaderCircle, Plus, RefreshCw, ShieldCheck, Unlink, UsersRound } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";

import { useLocale, useT } from "../i18n";
import { formatApiError } from "../api/errorDisplay";
import { createUploadTokenStore } from "../upload/persistence";
import {
  activateIdentityBinding,
  createIdentityBinding,
  deactivateIdentityBinding,
  fetchIdentityBindings,
  fetchIdentityMembers,
} from "./identityBindingsApi";
import { showcaseIdentityBindings } from "./identityData";
import { showcaseMembers } from "./memberData";
import { MemberDirectoryPanel } from "./MemberDirectoryPanel";

interface IdentityPageProps {
  showcaseMode?: boolean;
  canManage?: boolean;
  currentActorId?: string;
}

function formatDate(value: string, locale: "en" | "zh"): string {
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function errorText(error: unknown, fallback: string, requestIdLabel: string): string {
  return formatApiError(error, fallback, requestIdLabel);
}

export function IdentityPage({ showcaseMode = false, canManage = false, currentActorId }: IdentityPageProps) {
  const t = useT();
  const locale = useLocale();
  const queryClient = useQueryClient();
  const tokenStore = useMemo(() => createUploadTokenStore(sessionStorage), []);
  const token = tokenStore.load() ?? "";
  const enabled = showcaseMode || (token !== "" && canManage);
  const [issuer, setIssuer] = useState("");
  const [subject, setSubject] = useState("");
  const [memberSearch, setMemberSearch] = useState("");
  const [userId, setUserId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const bindings = useQuery({
    queryKey: ["identity-bindings", showcaseMode ? "showcase" : "live"],
    queryFn: () => showcaseMode
      ? Promise.resolve(showcaseIdentityBindings)
      : fetchIdentityBindings(token),
    enabled,
    retry: false,
    staleTime: showcaseMode ? Infinity : 15_000,
  });
  const createMutation = useMutation({
    mutationFn: () => createIdentityBinding(token, {
      issuer: issuer.trim(),
      subject: subject.trim(),
      userId: userId.trim(),
    }),
    onSuccess: () => {
      setIssuer("");
      setSubject("");
      setMemberSearch("");
      setUserId("");
      setFormError(null);
      void queryClient.invalidateQueries({ queryKey: ["identity-bindings"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
  });
  const members = useQuery({
    queryKey: ["identity-members", memberSearch.trim()],
    queryFn: ({ signal }) => showcaseMode
      ? Promise.resolve(showcaseMembers.filter((member) => member.isActive).map((member) => ({
          userId: member.userId,
          email: member.email,
          role: member.role,
        })))
      : fetchIdentityMembers(token, memberSearch, signal),
    enabled,
    retry: false,
    staleTime: showcaseMode ? Infinity : 15_000,
  });
  const deactivateMutation = useMutation({
    mutationFn: (bindingId: string) => deactivateIdentityBinding(token, bindingId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["identity-bindings"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
  });
  const activateMutation = useMutation({
    mutationFn: (bindingId: string) => activateIdentityBinding(token, bindingId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["identity-bindings"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
  });

  function submitBinding(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    if (!issuer.trim() || !subject.trim() || !userId.trim()) {
      setFormError(t("identity.required"));
      return;
    }
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(userId.trim())) {
      setFormError(t("identity.userIdInvalid"));
      return;
    }
    createMutation.mutate();
  }

  const items = bindings.data ?? [];
  const activeCount = items.filter((binding) => binding.isActive).length;
  const providerCount = new Set(items.map((binding) => binding.issuer)).size;
  const unavailable = !showcaseMode && !canManage
    ? t("identity.ownerOnly")
    : !showcaseMode && token === ""
      ? t("documents.authRequired")
      : null;

  return (
    <>
      <header className="product-page-header identity-page-header">
        <div>
          <p className="eyebrow">{t("identity.eyebrow")}</p>
          <h1>{t("identity.title")}</h1>
          <p className="page-summary">{t("identity.summary")}</p>
        </div>
        <div className="identity-mode-badge">
          <ShieldCheck aria-hidden="true" />
          {showcaseMode ? t("identity.snapshot") : canManage ? t("identity.ownerMode") : t("identity.readOnly")}
        </div>
      </header>

      <div className="identity-metrics" aria-label={t("identity.title")}>
        <div><span>{t("identity.total")}</span><strong>{enabled && bindings.data ? items.length : "-"}</strong></div>
        <div><span>{t("identity.active")}</span><strong>{enabled && bindings.data ? activeCount : "-"}</strong></div>
        <div><span>{t("identity.providers")}</span><strong>{enabled && bindings.data ? providerCount : "-"}</strong></div>
      </div>

      <section className="identity-management product-section" aria-labelledby="identity-bindings-title">
        <div className="identity-section-header">
          <div>
            <p className="eyebrow">{t("identity.bindingEyebrow")}</p>
            <h2 id="identity-bindings-title">{t("identity.bindings")}</h2>
            <p>{t("identity.bindingsSummary")}</p>
          </div>
          <button
            className="icon-button"
            type="button"
            aria-label={t("identity.refresh")}
            title={t("identity.refresh")}
            disabled={!enabled || bindings.isFetching}
            onClick={() => void bindings.refetch()}
          >
            <RefreshCw className={bindings.isFetching ? "spin" : undefined} aria-hidden="true" />
          </button>
        </div>

        {unavailable && <p className="permission-notice"><ShieldCheck aria-hidden="true" />{unavailable}</p>}
        {bindings.isPending && enabled && <div className="identity-state" role="status"><LoaderCircle className="spin" aria-hidden="true" />{t("identity.loading")}</div>}
        {bindings.isError && <div className="identity-state identity-error" role="alert"><CircleAlert aria-hidden="true" />{errorText(bindings.error, t("identity.loadError"), t("common.requestId"))}</div>}

        {enabled && bindings.isSuccess && (
          <>
            <form className="identity-binding-form" onSubmit={submitBinding}>
              <label className="access-field"><span>{t("identity.issuer")}</span><input type="url" value={issuer} onChange={(event) => setIssuer(event.target.value)} maxLength={512} placeholder="https://login.example.com/tenant" disabled={showcaseMode || createMutation.isPending} /></label>
              <label className="access-field"><span>{t("identity.subject")}</span><input value={subject} onChange={(event) => setSubject(event.target.value)} maxLength={512} placeholder="00u123example" disabled={showcaseMode || createMutation.isPending} /></label>
              <label className="access-field"><span>{t("identity.memberSearch")}</span><input type="search" value={memberSearch} onChange={(event) => { setMemberSearch(event.target.value); setUserId(""); }} placeholder={t("identity.memberSearchPlaceholder")} disabled={showcaseMode || createMutation.isPending} /></label>
              <label className="access-field"><span>{t("identity.member")}</span><select value={userId} onChange={(event) => setUserId(event.target.value)} disabled={showcaseMode || createMutation.isPending || members.isPending}><option value="">{members.isPending ? t("identity.membersLoading") : t("identity.memberSelect")}</option>{(members.data ?? []).map((member) => <option value={member.userId} key={member.userId}>{member.email} · {member.role === "owner" ? t("session.owner") : t("session.member")}</option>)}</select></label>
              <button className="secondary-button identity-create-button" type="submit" disabled={showcaseMode || createMutation.isPending}><Plus aria-hidden="true" />{createMutation.isPending ? t("identity.creating") : t("identity.create")}</button>
            </form>
            {formError && <p className="governance-inline-error" role="alert">{formError}</p>}
            {createMutation.error && <p className="governance-inline-error" role="alert">{errorText(createMutation.error, t("identity.createError"), t("common.requestId"))}</p>}
            {members.isError && <p className="governance-inline-error" role="alert">{errorText(members.error, t("identity.membersError"), t("common.requestId"))}</p>}
            {deactivateMutation.error && <p className="governance-inline-error" role="alert">{errorText(deactivateMutation.error, t("identity.deactivateError"), t("common.requestId"))}</p>}
            {activateMutation.error && <p className="governance-inline-error" role="alert">{errorText(activateMutation.error, t("identity.activateError"), t("common.requestId"))}</p>}

            {items.length === 0 ? (
              <div className="identity-empty"><KeyRound aria-hidden="true" /><div><h3>{t("identity.empty")}</h3><p>{t("identity.bindingsSummary")}</p></div></div>
            ) : (
              <div className="identity-binding-list">
                {items.map((binding) => (
                  <article className={binding.isActive ? "identity-binding-row" : "identity-binding-row inactive"} key={binding.bindingId}>
                    <div className="identity-binding-icon"><UsersRound aria-hidden="true" /></div>
                    <div className="identity-binding-user"><strong>{binding.userEmail}</strong><small>{binding.userId}</small></div>
                    <div className="identity-binding-provider"><span><Link2 aria-hidden="true" />{binding.issuer}</span><code>{binding.subject}</code></div>
                    <div className="identity-binding-state"><span className={binding.isActive ? "status-badge status-ready" : "status-badge"}>{binding.isActive ? t("identity.active") : t("identity.inactive")}</span><small>{formatDate(binding.updatedAt, locale)}</small></div>
                    <div className="identity-binding-action">
                      {binding.isActive && <button className="table-action danger-icon-button" type="button" disabled={showcaseMode || deactivateMutation.isPending} onClick={() => deactivateMutation.mutate(binding.bindingId)}><Unlink aria-hidden="true" />{t("identity.deactivate")}</button>}
                      {!binding.isActive && <button className="table-action" type="button" disabled={showcaseMode || activateMutation.isPending} onClick={() => activateMutation.mutate(binding.bindingId)}><Link2 aria-hidden="true" />{t("identity.activate")}</button>}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </>
        )}
      </section>

      <MemberDirectoryPanel
        showcaseMode={showcaseMode}
        canManage={canManage}
        currentActorId={currentActorId}
      />
    </>
  );
}
