import { demoErrorMessage } from "../demo/messages";
import { getLocale } from "../i18n";

const quotaMessages: Record<string, [string, string]> = {
  agent_usage_limit: ["当前 Agent 任务额度不足，部分额度可能仍由处理中或待审批任务占用。请查看企业用量或联系管理员。", "Agent task capacity is unavailable. Running tasks or pending approvals may reserve it. Check enterprise usage or contact your administrator."],
  agent_entitlement_inactive: ["当前企业尚无生效的 Agent 任务额度，请联系管理员配置。", "No Agent task allowance is active. Contact your administrator."],
  agent_usage_unavailable: ["暂时无法确认任务额度，请稍后重试。", "Task capacity could not be confirmed. Please try again shortly."],
  document_usage_limit: ["当前文档处理额度不足，请查看企业用量或联系管理员。", "Document processing capacity is unavailable. Check enterprise usage or contact your administrator."],
  document_entitlement_inactive: ["当前企业尚无生效的文档处理额度，请联系管理员配置后重试处理。", "No document processing allowance is active. Contact your administrator, then retry processing."],
  document_usage_unavailable: ["暂时无法确认文档处理额度，请稍后重试。", "Document processing capacity could not be confirmed. Please try again shortly."],
  document_provider_budget_exhausted: ["文档处理已达到模型调用上限，请联系管理员核查后再重试。文件仍保留。", "Document processing reached its model call limit. Your file is retained; contact your administrator before retrying."],
  provider_daily_budget_exhausted: ["当前企业今天的模型调用已达到上限，请联系管理员或稍后再试。", "Your enterprise has reached its daily model call limit. Contact your administrator or try again later."],
  provider_operation_budget_exhausted: ["此任务已达到模型调用上限，请联系管理员核查后再重试。", "This task reached its model call limit. Contact your administrator before retrying."],
};

export function quotaErrorMessage(code: string | null): string | null {
  return code && quotaMessages[code] ? quotaMessages[code][getLocale() === "zh" ? 0 : 1] : null;
}

export interface ApiErrorMetadata {
  code: string | null;
  requestId: string | null;
}

function readMetadata(error: unknown): ApiErrorMetadata {
  if (typeof error !== "object" || error === null) {
    return { code: null, requestId: null };
  }
  const candidate = error as { code?: unknown; requestId?: unknown };
  return {
    code: typeof candidate.code === "string" && candidate.code !== "" ? candidate.code : null,
    requestId: typeof candidate.requestId === "string" && candidate.requestId !== "" ? candidate.requestId : null,
  };
}

export function formatApiError(error: unknown, fallback: string, requestIdLabel: string): string {
  const message = error instanceof Error && error.message !== ""
    ? error.message
    : typeof error === "object" && error !== null && "message" in error && typeof error.message === "string" && error.message !== ""
      ? error.message
      : fallback;
  const metadata = readMetadata(error);
  const friendly = demoErrorMessage(metadata.code)
    ?? quotaErrorMessage(metadata.code);
  if (friendly) return metadata.requestId ? `${friendly} (${requestIdLabel}: ${metadata.requestId})` : friendly;
  const details = [metadata.code, metadata.requestId ? `${requestIdLabel}: ${metadata.requestId}` : null].filter(
    (value): value is string => value !== null,
  );
  return details.length > 0 ? `${message} (${details.join(" · ")})` : message;
}
