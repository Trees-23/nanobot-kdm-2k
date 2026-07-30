import {
  Activity,
  Copy,
  GitBranch,
  Link2,
  LocateFixed,
  Route,
  Timer,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import type { AuditGraphNode, TraceEdgeType } from "@/lib/audit-types";
import type { AuditEventItem } from "@/lib/audit-types";
import { auditNodeTypeLabel, auditStatusLabel, auditValueLabel } from "@/lib/audit-display";

export type TraceFocusMode = "causal" | "context" | "branch" | "resume" | null;

function valueOrDash(value: unknown): string {
  if (value == null || value === "") return "-";
  return String(value);
}

export function TraceNodeInspector({
  node,
  focusMode,
  onFocusMode,
  onClose,
  events,
  onLocateEvent,
}: {
  node: AuditGraphNode;
  focusMode: TraceFocusMode;
  onFocusMode: (mode: TraceFocusMode) => void;
  onClose: () => void;
  events: AuditEventItem[];
  onLocateEvent: (event: AuditEventItem) => void;
}) {
  const focusButtons: Array<{
    mode: Exclude<TraceFocusMode, null>;
    label: string;
    icon: typeof Link2;
  }> = [
    { mode: "causal", label: "因果链", icon: Link2 },
    { mode: "context", label: "执行上下文", icon: Activity },
    { mode: "branch", label: "结构分支", icon: GitBranch },
    { mode: "resume", label: "恢复链路", icon: Route },
  ];
  const suppressionReason = node.type === "delivery"
    && node.summary.delivery_result === "suppressed"
    ? node.summary.suppression_reason
      ? auditValueLabel(node.summary.suppression_reason)
      : "历史记录未提供原因"
    : null;
  const summaryRows = [
    ["类型", auditNodeTypeLabel(node.type)],
    ["终态", auditStatusLabel(node.terminal_status ?? node.status)],
    ["过程健康", auditStatusLabel(node.health_status ?? node.status)],
    ["Run 类型", node.run_kind],
    ["Lane", node.lane_id],
    ["Run ID", node.run_id],
    ["Iteration", node.iteration],
    ["Spawn Tool Call", node.spawn_tool_call_id],
    ["Continuation 来源", node.continuation_of_run_id],
    ["注入来源", node.injection_source],
    ["过程异常数", node.anomaly_count],
    ["耗时", node.elapsed_ms == null ? "-" : `${node.elapsed_ms} ms`],
    ["Provider", node.summary.provider],
    ["Model", node.summary.model],
    ["Tool", node.summary.tool_name],
    ["停止原因", node.summary.stop_reason ? auditValueLabel(node.summary.stop_reason) : null],
    ["Checkpoint 阶段", node.summary.checkpoint_phase ? auditValueLabel(node.summary.checkpoint_phase) : null],
    ["Checkpoint 版本", node.summary.checkpoint_version],
    ["Delivery 结果", node.summary.delivery_result ? auditValueLabel(node.summary.delivery_result) : null],
    ["抑制原因", suppressionReason],
  ];
  const contributingEvents = node.raw_event_ids
    .map((eventId) => events.find((event) => event.event_id === eventId))
    .filter((event): event is AuditEventItem => Boolean(event));

  return (
    <aside className="flex h-full min-h-0 w-full flex-col border-l border-border/60 bg-background" aria-label="节点检查器">
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-border/55 px-3">
        <div className="min-w-0">
          <h2 className="truncate text-[12.5px] font-semibold">{node.label}</h2>
          <p className="truncate font-mono text-[10px] text-muted-foreground">{node.id}</p>
        </div>
        <Button type="button" variant="ghost" size="icon" className="h-7 w-7" aria-label="关闭节点检查器" title="关闭节点检查器" onClick={onClose}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3 text-xs">
        <section>
          <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase text-muted-foreground">
            <Timer className="h-3.5 w-3.5" />摘要
          </h3>
          <dl className="divide-y divide-border/45">
            {summaryRows.map(([label, value]) => (
              <div key={label} className="grid grid-cols-[92px_minmax(0,1fr)] gap-3 py-2">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="min-w-0 break-words text-foreground">{valueOrDash(value)}</dd>
              </div>
            ))}
          </dl>
        </section>
        <section className="mt-5 border-t border-border/55 pt-4">
          <h3 className="mb-2 text-[11px] font-semibold uppercase text-muted-foreground">关系聚焦</h3>
          <div className="grid grid-cols-2 gap-1.5">
            {focusButtons.map(({ mode, label, icon: Icon }) => (
              <Button
                key={mode}
                type="button"
                variant={focusMode === mode ? "secondary" : "outline"}
                size="sm"
                className="h-8 justify-start gap-1.5 px-2 text-[11px]"
                onClick={() => onFocusMode(focusMode === mode ? null : mode)}
              >
                <Icon className="h-3.5 w-3.5" />{label}
              </Button>
            ))}
          </div>
        </section>
        <section className="mt-5 border-t border-border/55 pt-4">
          <h3 className="mb-2 text-[11px] font-semibold uppercase text-muted-foreground">原始事件</h3>
          <div className="divide-y divide-border/45 border-y border-border/45">
            {contributingEvents.map((event) => (
              <div key={event.event_id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 py-2">
                <button type="button" className="min-w-0 text-left" onClick={() => onLocateEvent(event)}>
                  <span className="block truncate text-[11px] font-medium">{auditValueLabel(event.event_type)}</span>
                  <span className="mt-0.5 block text-[10px] text-muted-foreground">
                    {new Date(event.occurred_at).toLocaleString()} · {auditValueLabel(event.status)}
                  </span>
                  <span className="mt-0.5 block truncate font-mono text-[10px] text-muted-foreground">
                    Turn {event.turn_id?.slice(0, 8) ?? "-"} · Run {event.run_id?.slice(0, 8) ?? "-"}
                    {event.iteration == null ? " · Run 级" : ` · Iteration ${event.iteration}`}
                  </span>
                  <span className="mt-0.5 block font-mono text-[10px] text-muted-foreground">
                    {event.event_id.slice(0, 12)} · {event.payload_id ? "Payload 可用" : "无 Payload"}
                  </span>
                </button>
                <div className="flex items-start gap-1">
                  <Button type="button" variant="ghost" size="icon" className="h-7 w-7" aria-label="复制 Event ID" title="复制 Event ID" onClick={() => void navigator.clipboard?.writeText(event.event_id)}>
                    <Copy className="h-3.5 w-3.5" />
                  </Button>
                  <Button type="button" variant="ghost" size="icon" className="h-7 w-7" aria-label="在时间线中定位" title="在时间线中定位" onClick={() => onLocateEvent(event)}>
                    <LocateFixed className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            ))}
            {!contributingEvents.length ? (
              <p className="py-2 text-[10.5px] text-muted-foreground">展开时间线后可查看结构化 Event 证据。</p>
            ) : null}
          </div>
        </section>
        {node.type === "checkpoint" && node.summary.transitions?.length ? (
          <section className="mt-5 border-t border-border/55 pt-4">
            <h3 className="mb-1 text-[11px] font-semibold uppercase text-muted-foreground">Checkpoint 转换</h3>
            <p className="mb-2 text-[10.5px] text-muted-foreground">
              {node.summary.checkpoint_cleared
                ? "该 Checkpoint 已在 Turn 完成后清理。"
                : node.summary.checkpoint_restored
                  ? "该 Checkpoint 已恢复，仍保留后续状态证据。"
                  : "该 Checkpoint 已写入，等待恢复或清理。"}
            </p>
            <div className="divide-y divide-border/45 border-y border-border/45">
              {node.summary.transitions.map((transition) => (
                <div key={`${transition.event_type}-${transition.occurred_at}`} className="flex justify-between gap-2 py-2 text-[10.5px]">
                  <span>{auditValueLabel(transition.event_type)}</span>
                  <span className="text-muted-foreground">v{transition.version ?? "-"} · {new Date(transition.occurred_at).toLocaleTimeString()}</span>
                </div>
              ))}
            </div>
          </section>
        ) : null}
        {node.relations.length ? (
          <section className="mt-5 border-t border-border/55 pt-4">
            <h3 className="mb-2 text-[11px] font-semibold uppercase text-muted-foreground">抑制关系</h3>
            {node.relations.map((relation, index) => (
              <div key={`${relation.raw_source_event_id}-${index}`} className="mb-2 rounded-md border border-border/55 px-2 py-1.5 text-[10.5px]">
                <p>{relation.type as TraceEdgeType} · {relation.resolution}</p>
                <p className="mt-0.5 truncate font-mono text-muted-foreground">{relation.raw_source_event_id}</p>
              </div>
            ))}
          </section>
        ) : null}
      </div>
    </aside>
  );
}
