import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Download, Files, LogOut, ShieldCheck } from "lucide-react";
import { z } from "zod";
import type { BrowserWorkspaceSession } from "../auth/BrowserSessionBoundary";
import { createApplicationCredentialStore } from "../auth/credentialStore";
import { authenticatedFetch } from "../auth/transport";
import { DocumentsPage } from "../product/DocumentsPage";
import { PresalesWorkspace } from "../presales/PresalesWorkspace";
import { useProductRoute } from "../product/routes";
import { setLocale, useLocale } from "../i18n";
import "./demo.css";

const usageSchema = z.object({
  attemptsUsed: z.number().int().min(0).max(6), attemptLimit: z.literal(6),
  uploadLimit: z.literal(6), maxFileBytes: z.number().int().positive(), storageBytes: z.number().int().positive(),
}).strict();

const requirements = "审计日志应保留不少于 180 天，并支持导出。\n单点登录应支持通过 OIDC 接入企业身份系统。\n数据应部署在中国境内，并提供客户自管加密密钥。";

export function PublicDemoWorkspace({ session }: { session: BrowserWorkspaceSession }) {
  const locale = useLocale();
  const zh = locale === "zh";
  const [route, navigate] = useProductRoute();
  const page = route === "presales" ? "presales" : "documents";
  const tokenStore = useMemo(() => createApplicationCredentialStore(sessionStorage), []);
  const credential = tokenStore.load();
  const contextKey = `demo:${session.tenant.tenantId}:${session.tenant.actorId}`;
  const [version, setVersion] = useState<string>();
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const usage = useQuery({
    queryKey: ["demo-usage", contextKey], enabled: credential !== null, retry: false, refetchInterval: 10_000,
    queryFn: async ({ signal }) => {
      if (!credential) throw new Error("Demo session expired");
      const response = await authenticatedFetch("/api/demo", credential, { signal });
      if (!response.ok) throw new Error("Demo usage is unavailable");
      return usageSchema.parse(await response.json());
    },
  });
  const expiry = session.demo ? new Date(session.demo.expiresAt).toLocaleTimeString(zh ? "zh-CN" : "en-US", { hour: "2-digit", minute: "2-digit" }) : "";
  return <div className="public-demo-shell">
    <header className="public-demo-topbar"><a href="#/documents" className="public-demo-brand">ED <span>{zh ? "企业文档 · 公开演示" : "Enterprise Docs · Live demo"}</span></a><div><button className="locale-button" onClick={() => setLocale(zh ? "en" : "zh")}>{zh ? "English" : "中文"}</button><button className="session-logout" onClick={session.logout}><LogOut aria-hidden="true" />{zh ? "退出演示" : "Exit demo"}</button></div></header>
    <main className="public-demo-content">
      <section className="public-demo-guide" aria-label={zh ? "演示指南" : "Demo guide"}>
        <div><span className="browser-entry-eyebrow">{zh ? "你的独立演示企业" : "YOUR OWN DEMO ENTERPRISE"}</span><h1>{zh ? "从资料到可交付的回应" : "From sources to reviewed responses"}</h1><p className="public-demo-company">{zh ? `当前企业：${session.tenant.name} · 演示访客` : "Current enterprise: Demo enterprise · Guest"}</p><p>{zh ? `资料、响应表和额度仅属于这家演示企业，与其他访客相互独立。演示于 ${expiry} 到期，随后自动清理。请使用示例或公开资料；需要保留的结果请及时导出。` : `Documents, response sheets and limits belong to your demo enterprise and are isolated from other visitors. Your demo expires at ${expiry} and is then cleaned automatically. Use samples or public documents and export results you want to keep.`}</p></div>
        <div className="public-demo-limits" role="status"><strong>{usage.data ? (zh ? `已尝试 ${usage.data.attemptsUsed} / 6 次生成` : `${usage.data.attemptsUsed} / 6 generation attempts used`) : (zh ? "最多 6 次生成尝试" : "Up to 6 generation attempts")}</strong><span>{zh ? "失败尝试也计入额度 · 最多 6 个文件 · 单个 2 MB · 总计 10 MB" : "Failed attempts count · Up to 6 files · 2 MB each · 10 MB total"}</span>{usage.isError && <span>{zh ? "暂时无法刷新剩余额度" : "Usage refresh is unavailable"}</span>}</div>
        <ol className="public-demo-steps"><li><strong>{zh ? "上传示例资料" : "Upload sample sources"}</strong><p>{zh ? "下载下面两份资料，在资料页一起上传，等待状态变为“就绪”。" : "Download both sources below, upload them together and wait until they are ready."}</p><div className="public-demo-downloads"><a href="/demo/product-guide.txt" download><Download aria-hidden="true" />{zh ? "产品说明" : "Product guide"}</a><a href="/demo/delivery-guide.txt" download><Download aria-hidden="true" />{zh ? "交付说明" : "Delivery guide"}</a></div></li><li><strong>{zh ? "填写客户要求" : "Enter customer requirements"}</strong><p>{zh ? "进入售前响应，选择资料并确认适用范围，将示例要求粘贴到表单。" : "Open Presales, select the sources, confirm applicability and paste the sample requirements."}</p><button type="button" className="browser-text-link" onClick={() => { void navigator.clipboard.writeText(requirements).then(() => { setCopied(true); setCopyFailed(false); }).catch(() => setCopyFailed(true)); }}>{copied ? (zh ? "已复制示例要求" : "Requirements copied") : (zh ? "复制示例要求" : "Copy sample requirements")}</button><a href="/demo/requirements.txt" download>{zh ? "下载问卷" : "Download questionnaire"}</a>{copyFailed && <p role="status">{zh ? "复制未完成，可下载问卷后粘贴。" : "Copy failed. Download the questionnaire and paste its text."}</p>}</li><li><strong>{zh ? "生成、复核并导出" : "Generate, review and export"}</strong><p>{zh ? "保存响应表后生成，展开每行核对原文引用，保存人工复核，再导出 CSV。最多可建 3 份表，每份 6 条要求。" : "Save the sheet, generate each response, check its citations, save your review and export CSV. Up to 3 sheets with 6 requirements each."}</p></li></ol>
      </section>
      <nav className="public-demo-nav" aria-label={zh ? "演示流程" : "Demo workflow"}><button aria-current={page === "documents" ? "page" : undefined} onClick={() => navigate("documents")}><Files aria-hidden="true" />{zh ? "1. 上传与管理资料" : "1. Documents"}</button><button aria-current={page === "presales" ? "page" : undefined} onClick={() => navigate("presales")}><ShieldCheck aria-hidden="true" />{zh ? "2. 生成与复核回应" : "2. Responses and review"}</button></nav>
      {page === "documents" ? <DocumentsPage demoMode contextKey={contextKey} canWrite navigate={next => navigate(next === "presales" ? "presales" : "documents")} onStartPresales={id => { setVersion(id); navigate("presales"); }} /> : <PresalesWorkspace maxRequirements={6} contextKey={contextKey} storageKey={`enterprise.presales.active:${session.tenant.tenantId}:${session.tenant.actorId}`} token={credential} openDocuments={() => navigate("documents")} initialVersionId={version} onInitialVersionConsumed={() => setVersion(undefined)} />}
    </main>
  </div>;
}
