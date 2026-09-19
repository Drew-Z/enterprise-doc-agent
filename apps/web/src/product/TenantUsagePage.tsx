import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, CircleAlert, Clock3, Database, Info, LoaderCircle, RefreshCw, ShieldCheck, UsersRound } from "lucide-react";

import { formatApiError } from "../api/errorDisplay";
import type { ApiCredential } from "../auth/transport";
import { useLocale } from "../i18n";
import type { ProductRoute } from "./routes";
import { fetchTenantUsage, TenantUsageApiError, type TenantUsage } from "./tenantUsageApi";
import "./tenantUsage.css";

const copy = {
  en: {
    title: "Enterprise usage", summary: "Generation capacity and resources for your current enterprise.", owner: "Administrator view",
    refresh: "Refresh usage", loading: "Loading usage…", checking: "Checking enterprise access…",
    forbidden: "Only enterprise administrators can view usage.", signIn: "Sign in and select an enterprise to view usage.",
    showcase: "Usage is available in a signed-in enterprise. Showcase mode has no live usage data.",
    sessionError: "Enterprise access could not be confirmed. Refresh your session and try again.", refreshSession: "Refresh session",
    loadError: "Usage could not be loaded. Refresh to try again.", invalid: "Usage data could not be read accurately. Refresh or contact your administrator.",
    changed: "The enterprise session has changed. Reopen usage in the current enterprise.", requestId: "Request ID",
    generation: "Generation capacity", active: "Active period", inactive: "No active generation period", legacy: "No generation period configured",
    inactiveDetail: "There is no generation entitlement in effect right now. New generations are unavailable; existing responses can still be reviewed and exported.",
    legacyDetail: "This enterprise has no configured generation period. Existing generation limits still apply. Period usage is not available here.",
    noCapacity: "No generation capacity available", noCapacityDetail: "Capacity may be used or reserved by work in progress. Refresh after running generations finish, or contact your administrator.",
    plan: "Plan", period: "Period", periodEnd: "End time excluded", used: "Used", reserved: "In progress", remaining: "Available", limit: "Period limit",
    noPeriod: "No current period", notConfigured: "Not configured", unavailable: "Unavailable",
    counting: "A generation uses one unit only after its response is saved successfully. In-progress generations reserve capacity; failed or cancelled work releases it.",
    storage: "Storage", storageLimit: "Storage limit", uploads: "Uploads in progress", storageNote: "Uploaded data and upload reservations share this storage limit.",
    members: "Member seats", seatLimit: "Seat limit", seatsUsed: "Active members", seatsRemaining: "Available seats",
    seatNote: "Active owner and member memberships occupy seats. Pending invitations do not.",
    manageDocuments: "Manage documents", manageMembers: "Manage members",
    activity: "Recent activity", activityLimit: "Up to 20 events from the current period.", activityList: "Recent usage activity",
    noActivity: "No recorded activity in this period.", noPeriodActivity: "Activity appears here when a generation period is active.",
    consume: "Generation saved", release: "Capacity released", quantity: "Units", estimatedCost: "Estimated model cost", unknown: "Unknown",
    costUnknown: "Model cost unknown", costKnown: "Some cost estimates available",
    costNote: "These are recorded estimates, not a bill or a complete period total. Released capacity does not mean the model provider charged nothing.",
    currencyUnknown: "Currency unknown",
  },
  zh: {
    title: "企业用量", summary: "查看当前企业的生成额度与资源使用情况。", owner: "管理员视图",
    refresh: "刷新用量", loading: "正在加载用量…", checking: "正在确认企业访问权限…",
    forbidden: "仅企业管理员可以查看用量。", signIn: "请先登录并选择企业，再查看用量。",
    showcase: "用量仅在已登录的企业中提供。展示模式没有实时用量数据。",
    sessionError: "无法确认当前企业的访问权限，请刷新会话后重试。", refreshSession: "刷新会话",
    loadError: "暂时无法加载用量，请刷新重试。", invalid: "无法准确读取用量数据，请刷新或联系管理员。",
    changed: "企业会话已变化，请在当前企业重新打开用量页面。", requestId: "请求编号",
    generation: "生成额度", active: "生效中", inactive: "当前没有生效中的生成周期", legacy: "尚未配置生成周期",
    inactiveDetail: "当前没有生效中的生成权益，暂时无法发起新生成；已有响应仍可复核和导出。",
    legacyDetail: "当前企业尚未配置生成周期，仍遵守已有生成限制。此处暂不提供周期用量。",
    noCapacity: "暂无可用生成额度", noCapacityDetail: "额度可能已使用，或被处理中任务预留。请在任务结束后刷新，或联系管理员。",
    plan: "计划", period: "周期", periodEnd: "不含结束时刻", used: "已使用", reserved: "处理中预留", remaining: "可用", limit: "周期上限",
    noPeriod: "无当前周期", notConfigured: "未配置", unavailable: "暂不可用",
    counting: "每次生成成功保存响应后消耗 1 份额度。处理中任务会预留额度，失败或取消后释放。",
    storage: "存储空间", storageLimit: "存储上限", uploads: "上传预留", storageNote: "已上传资料与上传中的预留空间共用存储上限。",
    members: "成员席位", seatLimit: "席位上限", seatsUsed: "活跃成员", seatsRemaining: "可用席位",
    seatNote: "有效的管理员和普通成员资格占用席位，待接受的邀请不占用席位。",
    manageDocuments: "管理资料", manageMembers: "管理成员",
    activity: "最近用量记录", activityLimit: "仅展示当前周期最近的至多 20 条记录。", activityList: "最近用量记录列表",
    noActivity: "当前周期暂无用量记录。", noPeriodActivity: "生成周期生效后，此处将显示该周期的记录。",
    consume: "生成已保存", release: "额度已释放", quantity: "额度数量", estimatedCost: "模型费用估算", unknown: "未知",
    costUnknown: "模型费用未知", costKnown: "已有部分费用估算",
    costNote: "这里只显示已记录的费用估算，不代表账单或完整周期总费用。额度释放也不表示模型服务商未收费。",
    currencyUnknown: "币种未知",
  },
} as const;

type Locale = "en" | "zh";

export interface TenantUsagePageProps {
  credential: ApiCredential | null;
  tenantId?: string;
  contextKey: string;
  canView: boolean;
  sessionPending?: boolean;
  sessionError?: boolean;
  showcaseMode?: boolean;
  onSessionRefresh?: () => void;
  navigate: (route: ProductRoute) => void;
}

function UsageHeading({ fetching = false, refresh }: { fetching?: boolean; refresh?: () => void }) {
  const text = copy[useLocale()];
  return <header className="product-page-header usage-header">
    <div><p className="eyebrow">{text.owner}</p><h1>{text.title}</h1><p className="page-summary">{text.summary}</p></div>
    {refresh && <button type="button" className="secondary-button" disabled={fetching} onClick={refresh}>
      <RefreshCw className={fetching ? "spin" : undefined} aria-hidden="true" />{text.refresh}
    </button>}
  </header>;
}

export function TenantUsagePage(props: TenantUsagePageProps) {
  const text = copy[useLocale()];
  const { credential, tenantId, contextKey, canView, showcaseMode, sessionPending, sessionError } = props;
  const notice = showcaseMode ? text.showcase : sessionPending ? text.checking : sessionError ? text.sessionError
    : !credential || !tenantId ? text.signIn : !canView ? text.forbidden : null;
  if (notice || !credential || !tenantId) {
    return <div className="usage-page">
      <UsageHeading />
      <div className="usage-notice" role={sessionPending || showcaseMode ? "status" : "alert"}>
        <ShieldCheck aria-hidden="true" /><p>{notice}</p>
        {sessionError && props.onSessionRefresh && <button type="button" className="secondary-button" onClick={props.onSessionRefresh}>{text.refreshSession}</button>}
      </div>
    </div>;
  }
  return <UsageContent key={`${tenantId}:${contextKey}`} credential={credential} tenantId={tenantId} contextKey={contextKey} navigate={props.navigate} />;
}

function UsageContent({ credential, tenantId, contextKey, navigate }: {
  credential: ApiCredential; tenantId: string; contextKey: string; navigate: (route: ProductRoute) => void;
}) {
  const locale = useLocale();
  const text = copy[locale];
  const query = useQuery({
    queryKey: ["tenant-usage", tenantId, contextKey],
    queryFn: ({ signal }) => fetchTenantUsage(credential, tenantId, signal),
    retry: false,
    gcTime: 0,
    staleTime: 0,
  });
  let errorMessage: string = text.loadError;
  if (query.error instanceof TenantUsageApiError) {
    const error = query.error;
    const message = error.status === 403 ? text.forbidden : error.status === 401 ? text.signIn
      : error.code === "tenant_usage_invalid_response" ? text.invalid
        : error.code === "tenant_usage_context_mismatch" ? text.changed : text.loadError;
    errorMessage = formatApiError({ message, requestId: error.requestId }, text.loadError, text.requestId);
  }
  return <div className="usage-page">
    <UsageHeading fetching={query.isFetching} refresh={() => void query.refetch()} />
    {query.isPending || query.isFetching ? <div className="usage-notice" role="status"><LoaderCircle className="spin" aria-hidden="true" /><p>{text.loading}</p></div>
      : query.isError ? <div className="usage-notice usage-error" role="alert"><CircleAlert aria-hidden="true" /><p>{errorMessage}</p></div>
        : <UsageDetails data={query.data} locale={locale} navigate={navigate} />}
  </div>;
}

function number(value: number, locale: Locale): string {
  return new Intl.NumberFormat(locale === "zh" ? "zh-CN" : "en-US").format(value);
}

function bytes(value: number, locale: Locale): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  const index = value === 0 ? 0 : Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${new Intl.NumberFormat(locale === "zh" ? "zh-CN" : "en-US", { maximumFractionDigits: 2 }).format(value / 1024 ** index)} ${units[index]}`;
}

function date(value: string, locale: Locale): string {
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-GB", {
    timeZone: "UTC", year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(new Date(value));
}

function Metric({ label, value, title, prominent = false }: { label: string; value: string; title?: string; prominent?: boolean }) {
  return <div className={prominent ? "usage-metric usage-metric-prominent" : "usage-metric"}>
    <dt>{label}</dt><dd title={title}>{value}</dd>
  </div>;
}

function UsageDetails({ data, locale, navigate }: { data: TenantUsage; locale: Locale; navigate: (route: ProductRoute) => void }) {
  const text = copy[locale];
  const active = data.entitlementStatus === "active";
  const exhausted = active && data.providerRequestsRemaining === 0;
  const resources = data.resources;
  const stateTitle = text[data.entitlementStatus];
  const unspecified = data.entitlementStatus === "legacy" ? text.notConfigured : text.noPeriod;
  const formatCount = (value: number | null) => value === null ? text.notConfigured : number(value, locale);
  const exactBytes = (value: number) => `${number(value, locale)} bytes`;
  return <>
    <section className="usage-period product-section" aria-labelledby="usage-generation-title">
      <div className="usage-section-heading">
        <h2 id="usage-generation-title">{text.generation}</h2>
        <span className={`usage-state-badge ${active && !exhausted ? "usage-active" : "usage-neutral"}`}><Clock3 aria-hidden="true" />{stateTitle}</span>
      </div>
      {active && data.periodStart && data.periodEnd && <dl className="usage-period-meta">
        <div><dt>{text.plan}</dt><dd>{data.planCode}</dd></div>
        <div><dt>{text.period}</dt><dd><time dateTime={data.periodStart}>{date(data.periodStart, locale)}</time><span> — </span><time dateTime={data.periodEnd}>{date(data.periodEnd, locale)}</time> UTC <small>· {text.periodEnd}</small></dd></div>
      </dl>}
      {!active && <p className="usage-period-notice">{data.entitlementStatus === "legacy" ? text.legacyDetail : text.inactiveDetail}</p>}
      {exhausted && <div className="usage-capacity-notice" role="status"><strong>{text.noCapacity}</strong><p>{text.noCapacityDetail}</p></div>}
      <dl className="usage-generation-metrics">
        <Metric label={text.remaining} value={active ? formatCount(data.providerRequestsRemaining) : data.entitlementStatus === "inactive" ? text.unavailable : text.notConfigured} prominent />
        <Metric label={text.used} value={active ? formatCount(data.providerRequestsUsed) : unspecified} />
        <Metric label={text.reserved} value={active ? formatCount(data.providerRequestsReserved) : unspecified} />
        <Metric label={text.limit} value={active ? formatCount(data.providerRequestLimit) : unspecified} />
      </dl>
      <p className="usage-explanation"><Info aria-hidden="true" /><span>{text.counting}</span></p>
    </section>

    <div className="usage-resources">
      <section className="usage-resource product-section" aria-labelledby="usage-storage-title">
        <div className="usage-section-heading"><h2 id="usage-storage-title"><Database aria-hidden="true" />{text.storage}</h2>
          <button type="button" className="usage-text-button" onClick={() => navigate("documents")}>{text.manageDocuments}<ArrowUpRight aria-hidden="true" /></button>
        </div>
        <p className="usage-resource-limit"><span>{text.storageLimit}</span><strong title={exactBytes(resources.storageLimitBytes)}>{bytes(resources.storageLimitBytes, locale)}</strong></p>
        <div className="usage-storage-bar" aria-hidden="true">
          <span style={{ width: `${resources.storageUsedBytes / resources.storageLimitBytes * 100}%` }} />
          <span className="usage-storage-reserved" style={{ width: `${resources.storageReservedBytes / resources.storageLimitBytes * 100}%` }} />
        </div>
        <dl className="usage-resource-metrics">
          <Metric label={text.used} value={bytes(resources.storageUsedBytes, locale)} title={exactBytes(resources.storageUsedBytes)} />
          <Metric label={text.uploads} value={bytes(resources.storageReservedBytes, locale)} title={exactBytes(resources.storageReservedBytes)} />
          <Metric label={text.remaining} value={bytes(resources.storageRemainingBytes, locale)} title={exactBytes(resources.storageRemainingBytes)} />
        </dl>
        <p className="usage-resource-note">{text.storageNote}</p>
      </section>
      <section className="usage-resource product-section" aria-labelledby="usage-seats-title">
        <div className="usage-section-heading"><h2 id="usage-seats-title"><UsersRound aria-hidden="true" />{text.members}</h2>
          <button type="button" className="usage-text-button" onClick={() => navigate("identity")}>{text.manageMembers}<ArrowUpRight aria-hidden="true" /></button>
        </div>
        <p className="usage-resource-limit"><span>{text.seatLimit}</span><strong>{formatCount(resources.seatLimit)}</strong></p>
        <dl className="usage-resource-metrics usage-seat-metrics">
          <Metric label={text.seatsUsed} value={formatCount(resources.seatsUsed)} />
          <Metric label={text.seatsRemaining} value={formatCount(resources.seatsRemaining)} />
        </dl>
        <p className="usage-resource-note">{text.seatNote}</p>
      </section>
    </div>

    <section className="usage-activity product-section" aria-labelledby="usage-activity-title">
      <div className="usage-section-heading"><div><h2 id="usage-activity-title">{text.activity}</h2><p className="usage-section-summary">{text.activityLimit}</p></div></div>
      <div className="usage-cost-note"><Info aria-hidden="true" /><div><strong>{data.costStatus === "known" ? text.costKnown : text.costUnknown}</strong><p>{text.costNote}</p></div></div>
      {data.recentEvents.length === 0 ? <p className="usage-empty">{active ? text.noActivity : text.noPeriodActivity}</p>
        : <ol className="usage-events" aria-label={text.activityList}>
          {data.recentEvents.map(event => <li key={`${event.operationId}:${event.eventType}`} className="usage-event">
            <div className="usage-event-description"><strong>{event.eventType === "consume" ? text.consume : text.release}</strong><time dateTime={event.occurredAt}>{date(event.occurredAt, locale)} UTC</time></div>
            <dl><Metric label={text.quantity} value={number(event.quantity, locale)} />
              <Metric label={text.estimatedCost} value={event.estimatedCost === null ? text.unknown : `${/^0+(?:\.0+)?(?:[eE][+-]?\d+)?$/.test(event.estimatedCost) ? "0" : event.estimatedCost} ${event.currency || text.currencyUnknown}`} />
            </dl>
          </li>)}
        </ol>}
    </section>
  </>;
}
