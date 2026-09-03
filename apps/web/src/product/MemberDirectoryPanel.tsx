import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2, Plus, Unlink, UsersRound } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";

import { useLocale, useT } from "../i18n";
import { formatApiError } from "../api/errorDisplay";
import { createUploadTokenStore } from "../upload/persistence";
import { showcaseMembers } from "./memberData";
import {
  activateTenantMember,
  changeTenantMemberRole,
  deactivateTenantMember,
  fetchTenantMembers,
  provisionTenantMember,
  type TenantMember,
  type TenantMemberRole,
} from "./membersApi";

interface MemberDirectoryPanelProps {
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

export function MemberDirectoryPanel({
  showcaseMode = false,
  canManage = false,
  currentActorId,
}: MemberDirectoryPanelProps) {
  const t = useT();
  const locale = useLocale();
  const queryClient = useQueryClient();
  const tokenStore = useMemo(() => createUploadTokenStore(sessionStorage), []);
  const token = tokenStore.load() ?? "";
  const enabled = showcaseMode || (token !== "" && canManage);
  const [query, setQuery] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<TenantMemberRole>("member");

  const members = useQuery({
    queryKey: ["tenant-members", showcaseMode ? "showcase" : "live", query.trim()],
    queryFn: ({ signal }) => showcaseMode
      ? Promise.resolve(showcaseMembers)
      : fetchTenantMembers(token, query, signal),
    enabled,
    retry: false,
    staleTime: showcaseMode ? Infinity : 15_000,
  });
  const provisionMutation = useMutation({
    mutationFn: () => provisionTenantMember(token, email.trim(), role),
    onSuccess: () => {
      setEmail("");
      setRole("member");
      invalidateIdentityQueries(queryClient);
    },
  });
  const memberMutation = useMutation({
    mutationFn: ({ action, member }: {
      action: "activate" | "deactivate" | "role";
      member: TenantMember;
    }) => {
      if (action === "activate") return activateTenantMember(token, member.membershipId);
      if (action === "deactivate") return deactivateTenantMember(token, member.membershipId);
      const nextRole = member.role === "owner" ? "member" : "owner";
      return changeTenantMemberRole(token, member.membershipId, nextRole);
    },
    onSuccess: () => invalidateIdentityQueries(queryClient),
  });

  function submitProvision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (email.trim()) provisionMutation.mutate();
  }

  const unavailable = !showcaseMode && !canManage
    ? t("identity.ownerOnly")
    : !showcaseMode && token === ""
      ? t("documents.authRequired")
      : null;

  return (
    <section className="identity-management product-section" aria-labelledby="tenant-members-title">
      <div className="identity-section-header">
        <div>
          <p className="eyebrow">{t("identity.directoryEyebrow")}</p>
          <h2 id="tenant-members-title">{t("identity.directory")}</h2>
          <p>{t("identity.directorySummary")}</p>
        </div>
        <span className="identity-mode-badge">
          <UsersRound aria-hidden="true" />
          {showcaseMode ? t("identity.snapshot") : t("identity.ownerMode")}
        </span>
      </div>

      {unavailable && <p className="permission-notice"><UsersRound aria-hidden="true" />{unavailable}</p>}
      {enabled && (
        <>
          <form className="identity-binding-form member-provision-form" onSubmit={submitProvision}>
            <label className="access-field"><span>{t("identity.memberEmail")}</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="person@company.com" disabled={showcaseMode || provisionMutation.isPending} /></label>
            <label className="access-field"><span>{t("identity.memberRole")}</span><select value={role} onChange={(event) => setRole(event.target.value as TenantMemberRole)} disabled={showcaseMode || provisionMutation.isPending}><option value="member">{t("session.member")}</option><option value="owner">{t("session.owner")}</option></select></label>
            <button className="secondary-button identity-create-button" type="submit" disabled={showcaseMode || provisionMutation.isPending || !email.trim()}><Plus aria-hidden="true" />{provisionMutation.isPending ? t("identity.provisioning") : t("identity.provision")}</button>
          </form>
          <label className="access-field directory-search"><span>{t("identity.directorySearch")}</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("identity.directorySearchPlaceholder")} disabled={showcaseMode || memberMutation.isPending} /></label>

          {members.isPending && <div className="identity-state" role="status">{t("identity.membersLoading")}</div>}
          {members.isError && <p className="governance-inline-error" role="alert">{errorText(members.error, t("identity.membersError"), t("common.requestId"))}</p>}
          {provisionMutation.error && <p className="governance-inline-error" role="alert">{errorText(provisionMutation.error, t("identity.provisionError"), t("common.requestId"))}</p>}
          {memberMutation.error && <p className="governance-inline-error" role="alert">{errorText(memberMutation.error, t("identity.memberMutationError"), t("common.requestId"))}</p>}

          {members.isSuccess && (
            <div className="identity-binding-list member-directory-list">
              {members.data.map((member) => {
                const isCurrent = member.userId === currentActorId;
                return (
                  <article className={member.isActive ? "identity-binding-row" : "identity-binding-row inactive"} key={member.membershipId}>
                    <div className="identity-binding-icon"><UsersRound aria-hidden="true" /></div>
                    <div className="identity-binding-user"><strong>{member.email}</strong><small>{member.userId}</small></div>
                    <div className="identity-binding-provider"><span>{t("identity.memberRole")}</span><code>{member.role === "owner" ? t("session.owner") : t("session.member")}</code></div>
                    <div className="identity-binding-state"><span className={member.isActive ? "status-badge status-ready" : "status-badge"}>{member.isActive ? t("identity.memberActive") : t("identity.memberInactive")}</span><small>{isCurrent ? t("identity.currentSession") : formatDate(member.updatedAt, locale)}</small></div>
                    <div className="identity-binding-action">
                      {member.isActive && <><button className="table-action" type="button" disabled={showcaseMode || memberMutation.isPending || isCurrent} onClick={() => memberMutation.mutate({ action: "role", member })}>{member.role === "owner" ? t("identity.demote") : t("identity.promote")}</button><button className="table-action danger-icon-button" type="button" disabled={showcaseMode || memberMutation.isPending || isCurrent} onClick={() => memberMutation.mutate({ action: "deactivate", member })}><Unlink aria-hidden="true" />{t("identity.deactivateMember")}</button></>}
                      {!member.isActive && <button className="table-action" type="button" disabled={showcaseMode || memberMutation.isPending} onClick={() => memberMutation.mutate({ action: "activate", member })}><Link2 aria-hidden="true" />{t("identity.activateMember")}</button>}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function invalidateIdentityQueries(queryClient: ReturnType<typeof useQueryClient>): void {
  void queryClient.invalidateQueries({ queryKey: ["tenant-members"] });
  void queryClient.invalidateQueries({ queryKey: ["identity-members"] });
  void queryClient.invalidateQueries({ queryKey: ["identity-bindings"] });
  void queryClient.invalidateQueries({ queryKey: ["audit-events"] });
}
