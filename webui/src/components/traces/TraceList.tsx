import {
  AlertCircle,
  CheckCircle2,
  CircleDashed,
  Clock3,
  LoaderCircle,
  PauseCircle,
  Search,
  SlidersHorizontal,
  ShieldAlert,
  TriangleAlert,
  XCircle,
} from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type {
  AuditIndexStatus,
  AuditTraceFilters,
  AuditTraceListItem,
  TraceDisplayStatus,
} from "@/lib/audit-types";
import { cn } from "@/lib/utils";
import { auditStatusLabel } from "@/lib/audit-display";

const TRACE_STATUSES: TraceDisplayStatus[] = [
  "running", "failed", "interrupted", "cancelled", "incomplete", "warning", "succeeded",
];

function StatusIcon({ status, className }: { status: TraceDisplayStatus; className?: string }) {
  const classes = cn("h-3.5 w-3.5 shrink-0", className);
  if (status === "succeeded") return <CheckCircle2 className={cn(classes, "text-emerald-600")} />;
  if (status === "failed") return <XCircle className={cn(classes, "text-destructive")} />;
  if (status === "running") return <LoaderCircle className={cn(classes, "animate-spin text-orange-500 motion-reduce:animate-none")} />;
  if (status === "warning") return <TriangleAlert className={cn(classes, "text-amber-600")} />;
  if (status === "interrupted") return <PauseCircle className={cn(classes, "text-blue-600")} />;
  if (status === "cancelled") return <Clock3 className={cn(classes, "text-muted-foreground")} />;
  return <CircleDashed className={cn(classes, "text-muted-foreground")} />;
}

function IndexBadge({ index }: { index: AuditIndexStatus | null }) {
  if (!index) return null;
  const stale = index.state === "stale";
  const ready = index.state === "ready";
  const lag = index.lag_ms == null ? null : index.lag_ms < 1_000
    ? "<1s"
    : `${Math.round(index.lag_ms / 1_000)}s`;
  return (
    <div
      className={cn(
        "inline-flex h-6 items-center gap-1.5 rounded-md border px-2 text-[11px] font-medium",
        ready && "border-emerald-500/25 text-emerald-700 dark:text-emerald-300",
        stale && "border-amber-500/30 text-amber-700 dark:text-amber-300",
      )}
      title={index.last_error?.message ?? undefined}
    >
      {ready ? <CheckCircle2 className="h-3 w-3" /> : <ShieldAlert className="h-3 w-3" />}
      <span>{stale ? "索引滞后" : index.state === "ready" ? "索引就绪" : index.state}</span>
      {lag ? <span className="font-mono opacity-70">{lag}</span> : null}
    </div>
  );
}

export function TraceList({
  items,
  filters,
  index,
  selectedTraceId,
  loading,
  loadingMore,
  hasMore,
  onFiltersChange,
  onSelect,
  onLoadMore,
}: {
  items: AuditTraceListItem[];
  filters: AuditTraceFilters;
  index: AuditIndexStatus | null;
  selectedTraceId: string | null;
  loading: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  onFiltersChange: (filters: AuditTraceFilters) => void;
  onSelect: (trace: AuditTraceListItem) => void;
  onLoadMore: () => void;
}) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const advancedCount = [filters.since, filters.until, filters.sourceType, filters.model, filters.tool]
    .filter(Boolean).length;

  return (
    <section className="flex h-full min-h-0 w-full flex-col border-r border-border/60 bg-background" aria-label="运行轨迹列表">
      <div className="space-y-2 border-b border-border/55 p-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            value={filters.query}
            onChange={(event) => onFiltersChange({ ...filters, query: event.target.value })}
            placeholder="搜索 Trace ID 或会话"
            aria-label="搜索运行轨迹"
            className="h-8 pl-8 text-xs"
          />
        </div>
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
          <select
            aria-label="按状态筛选"
            value={filters.status}
            onChange={(event) => onFiltersChange({
              ...filters,
              status: event.target.value as AuditTraceFilters["status"],
            })}
            className="h-8 min-w-0 rounded-md border border-input bg-background px-2 text-xs outline-none focus:ring-2 focus:ring-ring"
          >
            <option value="all">全部状态</option>
            {TRACE_STATUSES.map((status) => (
              <option key={status} value={status}>{auditStatusLabel(status)}</option>
            ))}
          </select>
          <label className="flex h-8 cursor-pointer items-center gap-1.5 rounded-md border border-input px-2 text-xs">
            <input
              type="checkbox"
              checked={filters.anomaliesOnly}
              onChange={(event) => onFiltersChange({ ...filters, anomaliesOnly: event.target.checked })}
              className="h-3.5 w-3.5 accent-orange-500"
            />
            仅异常
          </label>
        </div>
        <div className="flex items-center justify-between gap-2">
          <IndexBadge index={index} />
          <Button
            type="button"
            variant={advancedCount ? "secondary" : "ghost"}
            size="sm"
            className="h-7 gap-1.5 px-2 text-[11px]"
            aria-expanded={advancedOpen}
            aria-controls="trace-advanced-filters"
            onClick={() => setAdvancedOpen((value) => !value)}
          >
            <SlidersHorizontal className="h-3.5 w-3.5" />
            高级筛选{advancedCount ? ` ${advancedCount}` : ""}
          </Button>
        </div>
        {advancedOpen ? (
          <div id="trace-advanced-filters" className="space-y-2 border-t border-border/45 pt-2">
            <div className="grid grid-cols-2 gap-2">
              <Input
                type="date"
                value={filters.since}
                onChange={(event) => onFiltersChange({ ...filters, since: event.target.value })}
                aria-label="起始日期"
                className="h-8 min-w-0 px-2 text-[11px]"
              />
              <Input
                type="date"
                value={filters.until}
                onChange={(event) => onFiltersChange({ ...filters, until: event.target.value })}
                aria-label="结束日期"
                className="h-8 min-w-0 px-2 text-[11px]"
              />
            </div>
            <Input
              value={filters.sourceType}
              onChange={(event) => onFiltersChange({ ...filters, sourceType: event.target.value })}
              placeholder="Source，例如 websocket"
              aria-label="按来源筛选"
              className="h-8 text-xs"
            />
            <div className="grid grid-cols-2 gap-2">
              <Input
                value={filters.model}
                onChange={(event) => onFiltersChange({ ...filters, model: event.target.value })}
                placeholder="Model"
                aria-label="按模型筛选"
                className="h-8 min-w-0 text-xs"
              />
              <Input
                value={filters.tool}
                onChange={(event) => onFiltersChange({ ...filters, tool: event.target.value })}
                placeholder="Tool"
                aria-label="按工具筛选"
                className="h-8 min-w-0 text-xs"
              />
            </div>
          </div>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto" aria-busy={loading}>
        {loading ? (
          <div className="flex h-28 items-center justify-center gap-2 text-xs text-muted-foreground">
            <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" />
            正在读取索引
          </div>
        ) : items.length === 0 ? (
          <div className="flex h-32 flex-col items-center justify-center gap-2 px-6 text-center text-xs text-muted-foreground">
            <CircleDashed className="h-5 w-5" />
            <span>没有匹配的运行轨迹</span>
          </div>
        ) : (
          <div role="list" className="divide-y divide-border/45">
            {items.map((trace) => (
              <button
                type="button"
                role="listitem"
                key={trace.trace_id}
                aria-current={selectedTraceId === trace.trace_id ? "true" : undefined}
                onClick={() => onSelect(trace)}
                className={cn(
                  "group w-full px-3 py-3 text-left outline-none transition-colors",
                  "hover:bg-muted/45 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                  selectedTraceId === trace.trace_id && "bg-sidebar-accent/70",
                )}
              >
                <div className="flex items-start gap-2">
                  <StatusIcon status={trace.display_status} className="mt-0.5" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-2">
                      <span className="line-clamp-2 text-[12.5px] font-medium leading-4 text-foreground">
                        {trace.title}
                      </span>
                      <span className="shrink-0 text-[10.5px] text-muted-foreground">
                        {new Date(trace.last_seen).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                      </span>
                    </div>
                    <div className="mt-1.5 flex min-w-0 items-center gap-2 text-[10.5px] text-muted-foreground">
                      <span>{auditStatusLabel(trace.display_status)}</span>
                      <span>{trace.run_count} Runs</span>
                      {trace.anomaly_count > 0 ? (
                        <span className="inline-flex items-center gap-1 text-amber-700 dark:text-amber-300">
                          <AlertCircle className="h-3 w-3" />{trace.anomaly_count}
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-1 truncate font-mono text-[10px] text-muted-foreground/75">
                      {trace.trace_id}
                    </div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
      {hasMore ? (
        <div className="border-t border-border/55 p-2">
          <Button variant="ghost" size="sm" className="h-7 w-full text-xs" onClick={onLoadMore} disabled={loadingMore}>
            {loadingMore ? "正在加载" : "加载更多"}
          </Button>
        </div>
      ) : null}
    </section>
  );
}
