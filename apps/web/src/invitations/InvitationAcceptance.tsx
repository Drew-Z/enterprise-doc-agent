import { useEffect, useRef, useState } from "react";
import type { BrowserSessionController } from "../auth/sessionController";
import type { InvitationPreview } from "./schemas";

interface Props {
  controller: BrowserSessionController;
  code: string;
  setCode: (value: string) => void;
  email: string;
  busy: boolean;
  zh: boolean;
  onAccepted: () => void;
  onCancel: () => void;
  onLogout: () => void;
}

export function InvitationAcceptance({ controller, code, setCode, email, busy, zh, onAccepted, onCancel, onLogout }: Props) {
  const [preview, setPreview] = useState<InvitationPreview | null>(null);
  const request = useRef(0);
  useEffect(() => () => { request.current += 1; }, [code, controller]);
  return <div className="invitation-acceptance">
    <h2>{zh ? "加入已有企业" : "Join an enterprise"}</h2>
    <p className="browser-account">{email}</p>
    <p>{zh ? "使用受邀邮箱对应的登录账号，查看邀请后确认加入。" : "Use the account for the invited email, then review and accept the invitation."}</p>
    <form onSubmit={event => {
      event.preventDefault();
      if (busy) return;
      const attempt = ++request.current;
      if (preview) void controller.acceptInvitation(code).then(accepted => {
        if (request.current !== attempt) return;
        if (accepted) onAccepted(); else setPreview(null);
      });
      else void controller.inspectInvitation(code).then(result => { if (request.current === attempt) setPreview(result); });
    }}>
      {!preview && <label>{zh ? "邀请代码" : "Invitation code"}<input
        type="password" value={code} autoComplete="off" spellCheck={false} maxLength={48}
        disabled={busy} required
        onChange={event => { setCode(event.target.value); setPreview(null); }}
      /></label>}
      {preview && <div className="invitation-target">
        <span>{zh ? "你将加入" : "You are joining"}</span>
        <h3>{preview.tenantName}</h3>
        <p>{zh ? "新成员以普通成员身份加入，资料仍按企业访问权限开放。" : "New members join with the member role. Existing document access rules apply."}</p>
        <small>{zh ? "链接有效期：" : "Link expires: "}{new Intl.DateTimeFormat(zh ? "zh-CN" : "en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(preview.expiresAt))}</small>
      </div>}
      <button type="submit" className="browser-primary" disabled={busy || code.length !== 48}>
        {busy ? (zh ? "正在核验" : "Checking") : preview ? (zh ? "确认加入企业" : "Confirm membership") : (zh ? "查看邀请" : "Review invitation")}
      </button>
    </form>
    <div className="browser-card-actions">
      <button className="browser-text-link" disabled={busy} onClick={onCancel}>{zh ? "返回企业列表" : "Back to enterprises"}</button>
      <button className="browser-text-link" disabled={busy} onClick={onLogout}>{zh ? "退出并更换账号" : "Sign out to change account"}</button>
    </div>
  </div>;
}
