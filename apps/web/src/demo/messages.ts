import { getLocale } from "../i18n";

const messages: Record<string, [string, string]> = {
  demo_attempt_limit: ["本次演示的 6 次生成尝试已用完。你仍可复核和导出已有结果。", "All 6 generation attempts are used. You can still review and export existing results."],
  demo_daily_limit: ["今天的公开演示生成额度已用完，请明天再来。", "Today's public demo allowance is exhausted. Please return tomorrow."],
  demo_generation_busy: ["当前有其他演示正在生成，请稍后手动重试。", "Another demo is generating a response. Please try again shortly."],
  demo_upload_limit: ["演示最多上传 6 个文件，单个文件不能超过 2 MB。", "The demo accepts up to 6 files, each no larger than 2 MB."],
  demo_packet_limit: ["演示最多创建 3 份响应表，每份最多 6 条客户要求。", "The demo allows up to 3 response sheets with 6 requirements each."],
  demo_operation_forbidden: ["此功能需要正式企业工作区。", "This feature requires a regular workspace."],
};

export function demoErrorMessage(code: string | null): string | null {
  return code && messages[code] ? messages[code][getLocale() === "zh" ? 0 : 1] : null;
}
