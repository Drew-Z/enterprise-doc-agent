import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, RefreshCw, UsersRound } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useLocale } from "../i18n";
import type { ApiCredential } from "../auth/transport";
import { changeInvitation, createInvitation, fetchInvitations, InvitationsApiError } from "./client";
import type { InvitationList, InvitationMutation } from "./schemas";
import "./invitations.css";

export interface InvitationPanelProps {
  credential: ApiCredential;
  canManage: boolean;
}

type Notice = "created" | "copied" | "copy_failed" | "unconfirmed" | "conflict" | "unavailable" | "forbidden" | "limited" | "replayed" | "revoked";

export function InvitationPanel({ credential, canManage }: InvitationPanelProps) {
  const zh = useLocale() === "zh";
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [hasLink, setHasLink] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [retired, setRetired] = useState(false);
  const scope = useRef<AbortController | null>(null);
  const pending = useRef(false);
  const secret = useRef<{ invitationId: string; generation: number; token: string } | null>(null);
  const queryKey = ["membership-invitations", ...(typeof credential === "string" ? ["bearer"] : [credential.tenantId, credential.actorId])];
  const invitations = useQuery({
    queryKey, queryFn: ({ signal }) => fetchInvitations(credential, signal), enabled: canManage && !retired,
    retry: false, staleTime: 15_000,
  });

  useEffect(() => {
    const controller = new AbortController();
    scope.current = controller;
    secret.current = null;
    pending.current = false;
    setHasLink(false); setBusy(false); setNotice(null); setEmail(""); setUncertain(false); setRetired(false);
    const retire = () => {
      controller.abort(); secret.current = null; pending.current = false;
      setHasLink(false); setEmail(""); setNotice(null); setBusy(false); setRetired(true);
    };
    if (typeof credential !== "string") {
      credential.signal.addEventListener("abort", retire, { once: true });
      if (credential.signal.aborted) retire();
    }
    return () => {
      if (typeof credential !== "string") credential.signal.removeEventListener("abort", retire);
      controller.abort(); secret.current = null;
    };
  }, [credential, canManage]);

  useEffect(() => {
    const link = secret.current;
    if (link && !invitations.data?.items.some(item => item.invitationId === link.invitationId && item.generation === link.generation && item.state === "pending")) {
      secret.current = null; setHasLink(false);
    }
  }, [invitations.data]);

  function clearLink() { secret.current = null; setHasLink(false); }
  function current(controller: AbortController): boolean {
    return scope.current === controller && !controller.signal.aborted && (typeof credential === "string" || !credential.signal.aborted);
  }

  async function manage(write: (signal: AbortSignal) => Promise<InvitationMutation>, success: Notice) {
    const controller = scope.current;
    if (!controller || !current(controller) || pending.current || uncertain || !canManage || !invitations.data?.eligible) return;
    pending.current = true; setBusy(true); clearLink(); setNotice(null);
    try {
      await queryClient.cancelQueries({ queryKey });
      if (!current(controller)) return;
      const result = await write(controller.signal);
      if (!current(controller)) return;
      queryClient.setQueryData<InvitationList>(queryKey, previous => {
        if (!previous) return previous;
        const items = [result.invitation, ...previous.items.filter(item => item.invitationId !== result.invitation.invitationId)].sort((a, b) => {
          const rank = (state: string) => state === "pending" || state === "expired" ? 0 : 1;
          return rank(a.state) - rank(b.state) || b.createdAt.localeCompare(a.createdAt);
        });
        return { ...previous, items: items.slice(0, 100), hasMore: previous.hasMore || items.length > 100 };
      });
      if (result.token) {
        secret.current = { invitationId: result.invitation.invitationId, generation: result.invitation.generation, token: result.token };
        setHasLink(true);
      }
      setEmail(""); setNotice(result.replayed ? "replayed" : success);
      void queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    } catch (error) {
      if (!current(controller)) return;
      const code = error instanceof InvitationsApiError ? error.code : "";
      const known: Notice | null = ["invitation_conflict", "invitation_not_found", "invitation_busy"].includes(code) ? "conflict" : code === "invitation_limit_reached" ? "limited" : code === "invitation_forbidden" ? "forbidden" : ["invitations_disabled", "invitations_unavailable", "invitation_entitlement_required"].includes(code) ? "unavailable" : null;
      setNotice(known ?? "unconfirmed"); setUncertain(known === null || known === "conflict");
    } finally {
      if (current(controller)) { pending.current = false; setBusy(false); }
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void manage(signal => createInvitation(credential, email.trim(), crypto.randomUUID(), signal), "created");
  }

  async function copyLink() {
    const controller = scope.current;
    const link = secret.current;
    if (!controller || !current(controller) || !link) return;
    try {
      await navigator.clipboard.writeText(`${window.location.origin}/#/invitation?token=${link.token}`);
      if (current(controller) && secret.current === link) setNotice("copied");
    } catch { if (current(controller) && secret.current === link) setNotice("copy_failed"); }
  }

  async function refresh() {
    clearLink(); setNotice(null);
    const controller = scope.current;
    const result = await invitations.refetch();
    if (controller && current(controller) && result.isSuccess) setUncertain(false);
  }

  const messages: Record<Notice, string> = zh ? {
    created: "链接已生成。尚未发送邮件，请自行分享给受邀同事。",
    copied: "邀请链接已复制。尚未发送邮件，请自行分享给受邀同事。",
    copy_failed: "复制失败，请允许剪贴板访问后重试复制。",
    unconfirmed: "操作结果尚未确认。请先刷新邀请列表；若未收到链接，可重新生成，旧链接将失效。",
    conflict: "邀请已发生变化，请刷新列表后操作。", unavailable: "当前企业暂未开通邀请，请联系管理员。",
    forbidden: "当前账号没有管理邀请的权限。", limited: "邀请数量或本小时操作次数已达上限，请稍后再试。",
    replayed: "该操作已处理。若未收到链接，请重新生成。", revoked: "邀请已撤销，原链接无法再用于加入企业。",
  } : {
    created: "Link created. No email has been sent. Share it with your invited colleague.",
    copied: "Invitation link copied. No email has been sent. Share it with your invited colleague.",
    copy_failed: "Copy failed. Allow clipboard access and try copying again.",
    unconfirmed: "The result is unconfirmed. Refresh invitations first. If the link was not received, regenerate it to invalidate the old link.",
    conflict: "The invitation has changed. Refresh the list before continuing.", unavailable: "Invitations are not available for this enterprise. Contact an administrator.",
    forbidden: "This account cannot manage invitations.", limited: "The invitation or hourly operation limit has been reached. Try again later.",
    replayed: "This operation was already processed. Regenerate the invitation if you did not receive the link.", revoked: "Invitation revoked. The old link can no longer grant membership.",
  };

  return <section className="invitation-panel product-section" aria-labelledby="invitation-panel-title">
    <div className="identity-section-header">
      <div><p className="eyebrow">{zh ? "团队协作" : "Team access"}</p><h2 id="invitation-panel-title">{zh ? "邀请同事" : "Invite colleagues"}</h2><p>{zh ? "同事登录受邀邮箱对应的账号后确认加入，默认获得普通成员权限。" : "Colleagues sign in with the invited email and confirm joining with the member role."}</p></div>
      <button type="button" className="icon-button" title={zh ? "刷新邀请" : "Refresh invitations"} aria-label={zh ? "刷新邀请" : "Refresh invitations"} disabled={!canManage || busy || invitations.isFetching} onClick={() => void refresh()}><RefreshCw className={invitations.isFetching ? "spin" : undefined} aria-hidden="true" /></button>
    </div>
    {!canManage && <p className="permission-notice">{zh ? "仅企业所有者可以管理邀请。" : "Only enterprise owners can manage invitations."}</p>}
    {retired && <p role="status">{zh ? "会话已改变，请重新选择企业。" : "The session has changed. Select your enterprise again."}</p>}
    {canManage && !retired && invitations.isPending && <p role="status">{zh ? "正在加载邀请和成员占用" : "Loading invitations and member usage"}</p>}
    {canManage && !retired && invitations.isError && <p role="alert">{zh ? "无法加载邀请，请刷新重试或联系管理员确认已开通。" : "Invitations could not be loaded. Refresh or ask an administrator to enable them."}</p>}
    {canManage && !retired && invitations.isSuccess && <>
      <p className="invitation-seats"><UsersRound aria-hidden="true" />{zh ? "成员占用" : "Active members"} <strong>{invitations.data.seats.active} / {invitations.data.seats.limit ?? (zh ? "未配置" : "Not configured")}</strong><span>{zh ? "待接受邀请不占席位。" : "Pending invitations do not reserve seats."}</span></p>
      {!invitations.data.eligible && <p role="status">{messages.unavailable}</p>}
      {invitations.data.eligible && <form className="invitation-create-form" onSubmit={event => void submit(event)}>
        <label className="access-field"><span>{zh ? "受邀邮箱" : "Invited email"}</span><input type="email" required maxLength={320} value={email} onChange={event => setEmail(event.target.value)} placeholder="colleague@company.com" disabled={busy || uncertain} /></label>
        <button type="submit" className="primary-button" disabled={busy || uncertain || !email.trim()}>{busy ? (zh ? "正在处理" : "Working") : (zh ? "生成邀请链接" : "Create invitation link")}</button>
      </form>}
      {notice && <p role={notice === "unconfirmed" || notice === "copy_failed" ? "alert" : "status"}>{messages[notice]}</p>}
      {hasLink && <button type="button" className="secondary-button" onClick={() => void copyLink()}><Copy aria-hidden="true" />{zh ? "复制邀请链接" : "Copy invitation link"}</button>}
      {invitations.data.items.length === 0 && <p className="invitation-empty">{zh ? "尚无邀请。生成链接后，请自行分享；系统不会发送邮件。" : "No invitations yet. Create and share a link; no email will be sent."}</p>}
      <ul className="invitation-list">{invitations.data.items.map(item => <li key={item.invitationId}>
        <div className="invitation-recipient"><strong>{item.email}</strong><small>{zh ? "链接有效期：" : "Link expires: "}{new Intl.DateTimeFormat(zh ? "zh-CN" : "en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(item.expiresAt))}</small></div>
        <span className="status-badge">{zh ? ({ pending: "待接受", expired: "已过期", accepted: "已接受", revoked: "已撤销" }[item.state]) : item.state}</span>
        {(item.state === "pending" || item.state === "expired") && <div className="invitation-actions">
          <button type="button" className="table-action" disabled={busy || uncertain || !invitations.data.eligible} onClick={() => void manage(signal => changeInvitation(credential, item, "regenerate", crypto.randomUUID(), signal), "created")}>{zh ? "重新生成链接" : "Regenerate link"}</button>
          <button type="button" className="table-action danger-icon-button" disabled={busy || uncertain || !invitations.data.eligible} onClick={() => void manage(signal => changeInvitation(credential, item, "revoke", crypto.randomUUID(), signal), "revoked")}>{zh ? "撤销邀请" : "Revoke invitation"}</button>
          <small>{zh ? "重新生成或撤销会使旧链接失效。" : "Regenerating or revoking invalidates the old link."}</small>
        </div>}
      </li>)}</ul>
      {invitations.data.hasMore && <p>{zh ? "已显示全部待处理邀请及最近记录。" : "Showing all pending invitations and recent records."}</p>}
    </>}
  </section>;
}
