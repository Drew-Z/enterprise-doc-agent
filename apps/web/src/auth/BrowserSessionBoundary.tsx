import { useQueryClient } from "@tanstack/react-query";
import { Building2, ArrowRight, LoaderCircle, ShieldCheck, LogOut } from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { setLocale, useLocale } from "../i18n";
import { createSessionEvents } from "./events";
import { consumeAuthEntry, type AuthEntry } from "./entry";
import type { BrowserTenant } from "./schemas";
import { BrowserSessionController, type BrowserNotice } from "./sessionController";
import { InvitationAcceptance } from "../invitations/InvitationAcceptance";
import "../invitations/invitations.css";
import "./browser-session.css";

export interface BrowserWorkspaceSession {
  tenant: BrowserTenant;
  email: string;
  demo?: { expiresAt: string };
  chooseTenant: () => void;
  logout: () => void;
}

const notices: Record<BrowserNotice, [string, string]> = {
  demo_full: ["演示空间当前已满，请稍后再试。", "All demo spaces are occupied. Please try later."],
  demo_daily_limit: ["今天的公开演示额度已用完，请明天再来。", "Today's public demo allowance is exhausted. Please return tomorrow."],
  demo_start_unconfirmed: ["演示空间创建结果尚未确认，请核验登录状态后继续。", "Demo creation is unconfirmed. Check your session before continuing."],
  demo_signout_required: ["当前已有正式登录，请先退出再进入演示。", "Sign out of your regular account before starting a demo."],
  expired: ["登录已到期，请重新登录。", "Your session expired. Sign in again."],
  session_changed: ["登录或企业已变化，正在重新核验。", "Your session changed. Checking access again."],
  service_unavailable: ["暂时无法核验登录状态，请稍后重试。", "We could not check your session. Please try again."],
  selection_unconfirmed: ["企业切换尚未确认，请重新核验当前企业。", "The enterprise switch is unconfirmed. Check your current session."],
  admission_failed: ["开通未完成，请核对开通码、企业名称和登录账号后重试。", "Admission was not completed. Check the code, company name and signed-in account."],
  admission_complete: ["企业已开通，请选择企业进入。若列表未更新，可重新核验。", "Your enterprise is ready. Select it to continue, or refresh the list."],
  signed_out: ["已退出登录。", "You are signed out."],
  sign_in_failed: ["登录未完成，请重新登录。", "Sign-in could not be completed. Please try again."],
  github_email_required: ["请先在 GitHub 邮箱设置中验证主邮箱，再重新登录。", "Verify your primary email in GitHub email settings, then sign in again."],
  invitation_failed: ["邀请未通过核验，请检查原链接和当前登录账号。", "The invitation could not be verified. Check the original link and signed-in account."],
  invitation_complete: ["已加入企业，请选择企业进入。若列表未更新，可重新核验。", "You have joined the enterprise. Select it to continue, or refresh the list."],
  invitation_full: ["企业成员席位已满，请联系管理员后再试。", "All member seats are occupied. Contact your administrator."],
  invitation_account_conflict: ["此账号的身份关系需要管理员核对，邀请尚未接受。", "An administrator needs to review this account's identity links. The invitation was not accepted."],
  invitation_conflict: ["邀请状态已变化或正在处理，请重新查看邀请后再试。", "The invitation changed or is being processed. Review it before trying again."],
  invitation_unavailable: ["当前企业尚未开启邀请，请联系管理员。", "Invitations are not available for this enterprise. Contact your administrator."],
  invitation_unconfirmed: ["加入结果尚未确认。请刷新企业列表，或重新打开原链接核验；操作不会自动重发。", "Membership is unconfirmed. Refresh the enterprise list or reopen the original link to check. The action will not be retried automatically."],
};

export function BrowserSessionBoundary({ children, entry }: { children: (session: BrowserWorkspaceSession) => ReactNode; entry?: AuthEntry }) {
  const queryClient = useQueryClient();
  const locale = useLocale();
  const zh = locale === "zh";
  const eventsRef = useRef<ReturnType<typeof createSessionEvents> | null>(null);
  const controller = useMemo(() => new BrowserSessionController({
    clearWorkspace: () => { void queryClient.cancelQueries(); queryClient.clear(); },
    broadcast: () => eventsRef.current?.publish(),
  }), [queryClient]);
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  const demoSession = state.session !== null && "demo" in state.session;
  const [code, setCode] = useState(entry?.admissionToken ?? "");
  const [company, setCompany] = useState("");
  const [invitationCode, setInvitationCode] = useState(entry?.invitationToken ?? "");
  const [invitationOpen, setInvitationOpen] = useState(Boolean(entry?.invitationLink));
  const [invitationNeedsLogin, setInvitationNeedsLogin] = useState(Boolean(entry?.invitationLink));
  const [invitationRevision, setInvitationRevision] = useState(0);
  const contextRef = useRef<string | null>(null);

  useEffect(() => { entry?.releaseSecrets?.(); }, [entry]);
  useEffect(() => {
    const context = state.session?.contextVersion ?? null;
    if (
      ["anonymous", "disabled", "unavailable", "logout-unconfirmed"].includes(state.phase)
      || state.notice === "session_changed"
      || (contextRef.current !== null && context !== contextRef.current)
    ) {
      setInvitationCode("");
      setInvitationOpen(false);
    }
    if (demoSession) setInvitationOpen(false);
    if (context && ["choosing", "working"].includes(state.phase)) setInvitationNeedsLogin(false);
    contextRef.current = context;
  }, [state.phase, state.notice, state.session?.contextVersion, demoSession]);
  useEffect(() => {
    if (invitationOpen && state.phase === "working") void controller.showTenants();
  }, [controller, invitationOpen, state.phase]);

  useEffect(() => {
    eventsRef.current = createSessionEvents(() => { void controller.reconcile("session_changed"); });
    const visible = () => {
      if (document.visibilityState === "visible") void controller.reconcile();
    };
    const shown = () => { void controller.reconcile(); };
    const invitationLink = () => {
      if (!window.location.hash.startsWith("#/invitation")) return;
      const next = consumeAuthEntry();
      if (!next.invitationLink) return;
      const snapshot = controller.getSnapshot();
      const authenticated = snapshot.session !== null && !("demo" in snapshot.session) && ["choosing", "working", "busy"].includes(snapshot.phase);
      setInvitationCode(authenticated ? next.invitationToken ?? "" : "");
      setInvitationOpen(authenticated);
      setInvitationNeedsLogin(!authenticated);
      setInvitationRevision(value => value + 1);
      next.releaseSecrets?.();
      if (authenticated) void controller.showTenants();
    };
    document.addEventListener("visibilitychange", visible);
    window.addEventListener("pageshow", shown);
    window.addEventListener("hashchange", invitationLink);
    controller.start();
    return () => {
      document.removeEventListener("visibilitychange", visible);
      window.removeEventListener("pageshow", shown);
      window.removeEventListener("hashchange", invitationLink);
      eventsRef.current?.close();
      eventsRef.current = null;
      controller.stop();
    };
  }, [controller]);

  useEffect(() => { if (["anonymous", "disabled", "logout-unconfirmed"].includes(state.phase)) setCode(""); }, [state.phase]);

  if (state.phase === "working" && state.session?.currentTenant && !invitationOpen) {
    return <Fragment key={state.workspaceKey}><div hidden={state.verifying} inert={state.verifying}>{children({ tenant: state.session.currentTenant, email: state.session.email ?? (zh ? "演示访客" : "Demo visitor"), demo: "demo" in state.session ? { expiresAt: state.session.expiresAt } : undefined, chooseTenant: () => { void controller.showTenants(); }, logout: () => { void controller.logout(); } })}</div>{state.verifying && <main className="browser-entry"><div className="browser-wait" role="status"><LoaderCircle className="spin" aria-hidden="true" /><h2>{zh ? "正在核验，请稍候" : "Checking your session"}</h2></div></main>}</Fragment>;
  }
  const notice = state.notice ?? entry?.signInError ?? (entry?.signInFailed ? "sign_in_failed" : null);
  const github = state.loginProvider === "github";
  return <main className="browser-entry">
    <header className="browser-entry-top"><div className="browser-entry-brand"><span aria-hidden="true">ED</span><strong>{zh ? "企业文档工作台" : "Enterprise Docs"}</strong></div><button className="locale-button" onClick={() => setLocale(zh ? "en" : "zh")}>{zh ? "English" : "中文"}</button></header>
    <div className="browser-entry-layout">
      <section className="browser-entry-intro"><span className="browser-entry-eyebrow">{zh ? "企业资料 · 售前协作" : "DOCUMENTS · PRESALES"}</span><h1>{zh ? <>让每一条回应，<br />都有据可依。</> : <>Make every answer<br />grounded in evidence.</>}</h1><p>{zh ? "在企业工作区管理资料、核对客户要求，并将经过复核的回应交付给客户。" : "Manage company documents, check customer requirements and deliver reviewed responses from your enterprise workspace."}</p><div className="browser-entry-assurance"><ShieldCheck aria-hidden="true" /><span>{zh ? "按企业授权访问，保留证据与复核记录。" : "Enterprise access with evidence and review history."}</span></div></section>
      <section className="browser-entry-card" aria-label={zh ? "企业登录" : "Enterprise sign-in"}>
        {state.phase === "anonymous" && state.demoAvailable && <div className="browser-demo-entry"><span className="browser-entry-eyebrow">{zh ? "无需注册 · 独立演示企业" : "NO SIGN-UP · YOUR OWN DEMO ENTERPRISE"}</span><h2>{zh ? "先体验完整流程" : "Try the complete workflow"}</h2><p>{zh ? "自动分配演示企业，上传资料、生成回应、核查引用，再导出复核结果。演示有效期 2 小时，含 6 次生成尝试。" : "Get your own demo enterprise, upload sources, generate responses, check citations and export reviewed results. Your demo lasts 2 hours with 6 generation attempts."}</p><button type="button" className="browser-primary" onClick={() => void controller.startDemo()}>{zh ? "一键进入演示" : "Try the live demo"}<ArrowRight aria-hidden="true" /></button></div>}
        {notice && <p className="browser-notice" role={notice.endsWith("failed") || notice.endsWith("unconfirmed") ? "alert" : "status"}>{notices[notice][zh ? 0 : 1]}</p>}
        {(state.phase === "loading" || state.phase === "busy") && <div className="browser-wait" role="status"><LoaderCircle className="spin" aria-hidden="true" /><h2>{zh ? "正在核验，请稍候" : "Checking your session"}</h2></div>}
        {state.phase === "anonymous" && <><span className="browser-card-icon"><Building2 aria-hidden="true" /></span><h2>{zh ? "登录工作台" : "Sign in to your workspace"}</h2><p>{github ? (zh ? "使用 GitHub 账号登录，然后选择你有权访问的企业。" : "Sign in with GitHub, then select an authorized enterprise.") : (zh ? "使用企业账号登录，然后选择你有权访问的企业。" : "Sign in with your company account, then select an authorized enterprise.")}</p>{notice === "github_email_required" && <a className="browser-text-link" href="https://github.com/settings/emails" target="_blank" rel="noopener noreferrer">{zh ? "打开 GitHub 邮箱设置" : "Open GitHub email settings"}</a>}{entry?.admissionLink && <p className="browser-notice">{zh ? "请先登录，再重新打开原开通链接。" : "Sign in first, then reopen your admission link."}</p>}<a className="browser-primary" href="/auth/login" onClick={() => controller.suspend()}>{github ? (zh ? "使用 GitHub 登录" : "Sign in with GitHub") : (zh ? "使用企业账号登录" : "Sign in with your company account")}<ArrowRight aria-hidden="true" /></a></>}
        {state.phase === "disabled" && <><h2>{zh ? "登录服务尚未开启" : "Sign-in is not enabled"}</h2><p>{zh ? "请联系服务管理员开通企业登录。" : "Contact the service administrator to enable company sign-in."}</p><button className="browser-secondary" onClick={() => void controller.reconcile()}>{zh ? "重新核验" : "Check again"}</button></>}
        {state.phase === "unavailable" && <><h2>{zh ? "暂时无法进入工作区" : "The workspace is unavailable"}</h2><p>{zh ? "核验完成后再继续操作。已提交的操作不会自动重发。" : "Check your session before continuing. Submitted actions are not retried automatically."}</p><button className="browser-primary" onClick={() => void controller.reconcile()}>{zh ? "重新核验登录状态" : "Check sign-in status"}</button><a className="browser-text-link" href="/auth/login">{zh ? "重新登录" : "Sign in again"}</a></>}
        {state.phase === "logout-unconfirmed" && <><h2>{zh ? "服务端退出尚未确认" : "Sign-out is unconfirmed"}</h2><p role="alert">{zh ? "工作区已关闭。网络恢复后，请重试退出或主动核验登录状态。" : "The workspace is closed. Retry sign-out or explicitly check your session when connectivity returns."}</p><button className="browser-primary" onClick={() => void controller.logout()}>{zh ? "重试退出" : "Retry sign-out"}</button><button className="browser-secondary" onClick={() => void controller.verifyAfterLogout()}>{zh ? "核验登录状态" : "Check sign-in status"}</button></>}
        {state.phase === "choosing" && state.session && !invitationOpen && <><h2>{zh ? "选择企业" : "Choose an enterprise"}</h2><p className="browser-account">{state.session.email}</p><div className="browser-tenant-list">{state.tenants.map(tenant => <button key={tenant.tenantId} className="browser-tenant" aria-label={(zh ? "进入 " : "Open ") + tenant.name} onClick={() => void controller.selectTenant(tenant.tenantId)}><Building2 aria-hidden="true" /><span><strong>{tenant.name}</strong><small>{tenant.role === "owner" ? (zh ? "企业管理员" : "Administrator") : (zh ? "成员" : "Member")}</small></span><ArrowRight aria-hidden="true" /></button>)}</div>{state.tenants.length === 0 && <p className="browser-empty">{zh ? "此账号暂无可访问的企业。已有企业请联系企业管理员；开通新企业请使用有效开通码。" : "This account has no enterprise access. Contact your administrator, or use an admission code to open a new enterprise."}</p>}<details className="browser-admission" open={Boolean(entry?.admissionLink)}><summary>{zh ? "开通新企业" : "Open a new enterprise"}</summary><form onSubmit={event => { event.preventDefault(); void controller.acceptAdmission(code, company).then(accepted => { if (accepted) setCode(""); }); }}><label>{zh ? "开通码" : "Admission code"}<input type="password" autoComplete="off" spellCheck={false} maxLength={48} value={code} onChange={event => setCode(event.target.value)} required /></label><label>{zh ? "企业名称" : "Company name"}<input value={company} onChange={event => setCompany(event.target.value)} maxLength={200} required /></label><button className="browser-primary" type="submit">{zh ? "确认开通" : "Confirm admission"}</button></form></details><div className="browser-card-actions"><button className="browser-text-link" onClick={() => void controller.showTenants()}>{zh ? "刷新企业列表" : "Refresh enterprises"}</button><button className="browser-text-link" onClick={() => void controller.logout()}><LogOut aria-hidden="true" />{zh ? "退出登录" : "Sign out"}</button></div></>}
        {state.phase === "anonymous" && invitationNeedsLogin && <p className="browser-notice">{zh ? "请先登录，再重新打开原邀请链接。" : "Sign in first, then reopen the original invitation link."}</p>}
        {state.phase === "choosing" && !invitationOpen && <button className="browser-secondary" onClick={() => setInvitationOpen(true)}>{zh ? "加入已有企业" : "Join an enterprise"}</button>}
        {["choosing", "busy"].includes(state.phase) && state.session && invitationOpen && <InvitationAcceptance
          key={invitationRevision}
          controller={controller} code={invitationCode} setCode={setInvitationCode}
          email={state.session.email ?? ""} busy={state.phase === "busy"} zh={zh}
          onAccepted={() => { setInvitationCode(""); setInvitationOpen(false); }}
          onCancel={() => { setInvitationCode(""); setInvitationOpen(false); void controller.showTenants(); }}
          onLogout={() => { setInvitationCode(""); setInvitationOpen(false); void controller.logout(); }}
        />}
      </section>
    </div>
    <footer className="browser-entry-footer">{zh ? "从资料到回应，每一步都能追溯。" : "From source documents to responses, keep every step traceable."}</footer>
  </main>;
}
