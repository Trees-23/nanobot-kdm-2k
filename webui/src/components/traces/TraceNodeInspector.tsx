import {
  Activity,
  Copy,
  Eye,
  GitBranch,
  Link2,
  LocateFixed,
  Route,
  Send,
  Timer,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import type { AuditGraphNode, AuditNodeEventRef, TraceEdgeType } from "@/lib/audit-types";
import { auditStatusLabel, auditValueLabel } from "@/lib/audit-display";

export type TraceFocusMode = "causal" | "context" | "branch" | "result" | "recovery" | null;

export function TraceNodeInspector({
  node,
  focusMode,
  onFocusMode,
  onClose,
  onLocateEvent,
  onLoadPayload,
}: {
  node: AuditGraphNode;
  focusMode: TraceFocusMode;
  onFocusMode: (mode: TraceFocusMode) => void;
  onClose: () => void;
  onLocateEvent: (event: AuditNodeEventRef) => void;
  onLoadPayload: (payloadId: string) => void;
}) {
  const focusButtons: Array<{
    mode: Exclude<TraceFocusMode, null>;
    label: string;
    icon: typeof Link2;
  }> = [
    { mode: "causal", label: "因果链", icon: Link2 },
    { mode: "context", label: "执行上下文", icon: Activity },
    { mode: "branch", label: "结构分支", icon: GitBranch },
    { mode: "result", label: "结果回传", icon: Send },
    { mode: "recovery", label: "恢复关系", icon: Route },
  ];
  const suppressionReason = node.type === "delivery"
    && node.summary.delivery_result === "suppressed"
    ? node.summary.suppression_reason
      ? auditValueLabel(node.summary.suppression_reason)
      : "历史记录未提供原因"
    : null;
  const commonRows: Array<[string, unknown]> = [
    ["终态", auditStatusLabel(node.terminal_status ?? node.status)],
    ["耗时", node.elapsed_ms == null ? null : `${node.elapsed_ms} ms`],
  ];
  const rowsByType: Partial<Record<AuditGraphNode["type"], Array<[string, unknown]>>> = {
    task: [
      ["Task ID", node.summary.task_id],
      ["状态", node.summary.task_status ? auditValueLabel(node.summary.task_status) : null],
      ["执行阶段", node.summary.task_phase ? auditValueLabel(node.summary.task_phase) : null],
      ["终止状态", node.summary.termination_state ? auditValueLabel(node.summary.termination_state) : null],
      ["交付阶段", node.summary.delivery_phase ? auditValueLabel(node.summary.delivery_phase) : null],
      ["Required", node.summary.required_task],
      ["Revision", node.summary.task_revision],
      ["生命周期事件", node.summary.lifecycle_event_count],
      ["Owner Run", node.summary.owner_run_id],
      ["Child Run", node.summary.child_run_id],
      ["替换 Task", node.summary.replaces_task_id],
    ],
    tool_call: [
      ["Tool", node.summary.tool_name],
      ["安全输入", node.summary.safe_input_summary],
      ["错误类型", node.summary.error_type],
      ["错误码", node.summary.error_code],
      ["错误来源", node.summary.error_source],
      ["可重试性", node.summary.retryability],
      ["操作证据", node.summary.operation_evidence_kind],
      ["恢复证据", node.summary.recovery_evidence_kind],
      ["有效 timeout", node.summary.effective_timeout_ms == null ? null : `${node.summary.effective_timeout_ms} ms`],
      ["Iteration", node.iteration],
      ["Run ID", node.run_id],
    ],
    run: [
      ["Run 类型", node.run_kind],
      ["停止原因", node.summary.stop_reason ? auditValueLabel(node.summary.stop_reason) : null],
      ["工具错误数", node.summary.failure_count],
      ["致命错误数", node.summary.fatal_failure_count],
      ["已恢复数", node.summary.recovered_failure_count],
      ["继续运行数", node.summary.continued_failure_count],
      ["失败策略", node.summary.failure_policy],
      ["failOnToolError", node.summary.fail_on_tool_error],
    ],
    model_call: [
      ["Provider", node.summary.provider],
      ["Model", node.summary.model],
      ["Prompt tokens", node.summary.prompt_tokens],
      ["Completion tokens", node.summary.completion_tokens],
      ["Attempt", node.summary.attempt_count],
    ],
    model_attempt: [
      ["Provider", node.summary.provider],
      ["Model", node.summary.model],
      ["Attempt", node.summary.attempt_count],
    ],
    delivery: [
      ["Delivery 结果", node.summary.delivery_result ? auditValueLabel(node.summary.delivery_result) : null],
      ["抑制原因", suppressionReason],
    ],
    checkpoint: [
      ["阶段", node.summary.checkpoint_phase ? auditValueLabel(node.summary.checkpoint_phase) : null],
      ["版本", node.summary.checkpoint_version],
      ["已恢复", node.summary.checkpoint_restored],
      ["已清理", node.summary.checkpoint_cleared],
    ],
    decision: [
      ["决策类型", node.summary.decision_type ? auditValueLabel(node.summary.decision_type) : null],
      ["原因", node.summary.reason],
      ["Iteration", node.iteration],
    ],
    goal: [["标识", node.summary.identifier]],
    turn_result: [["标识", node.summary.identifier]],
    anomaly: [["异常类型", node.summary.subtype]],
    external_reference: [["标识", node.summary.identifier]],
  };
  const summaryRows = [...commonRows, ...(rowsByType[node.type] ?? [])]
    .filter((row) => row[1] != null && row[1] !== "");
  const contributingEvents = node.raw_events ?? [];
  const impactLabel = {
    run_failed: "导致 Run 失败",
    run_continued: "Run 继续执行",
    unknown: "影响未知",
    pending: "影响待定",
  }[node.summary.impact ?? "unknown"];
  const recoveryLabel = {
    recovered: "已由后续确定性调用恢复",
    unrecovered: "未恢复",
    continued: "未证明恢复，但 Run 已继续",
    unresolved: "证据不足，恢复状态未决",
    pending: "恢复状态待定",
  }[node.summary.recovery_status ?? "unresolved"];
  const errorMessage = node.summary.error_message ?? node.summary.error_summary;
  const isFailedTool = node.type === "tool_call" && Boolean(
    node.summary.failure_kind
    || errorMessage
    || ["failed", "warning", "cancelled", "interrupted"].includes(node.status),
  );

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
        {isFailedTool ? (
          <section className="mb-4 rounded-md border border-destructive/30 bg-destructive/5 p-3">
            <h3 className="text-[11px] font-semibold text-destructive">根因</h3>
            <p className="mt-1 whitespace-pre-wrap break-words text-[12px] font-medium">
              {errorMessage ?? "历史版本未记录错误详情"}
            </p>
            <p className="mt-2 text-[10.5px] text-muted-foreground">影响：{impactLabel}</p>
            <p className="mt-1 text-[10.5px] text-muted-foreground">恢复：{recoveryLabel}</p>
            {node.summary.evidence_source === "legacy_inferred" ? (
              <p className="mt-2 text-[10px] text-amber-700 dark:text-amber-300">此分类由历史唯一证据保守推断。</p>
            ) : null}
          </section>
        ) : null}
        <section>
          <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase text-muted-foreground">
            <Timer className="h-3.5 w-3.5" />摘要
          </h3>
          <dl className="divide-y divide-border/45">
            {summaryRows.map(([label, value]) => (
              <div key={label} className="grid grid-cols-[92px_minmax(0,1fr)] gap-3 py-2">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="min-w-0 break-words text-foreground">{String(value)}</dd>
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
                aria-pressed={focusMode === mode}
                title={`聚焦${label}`}
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
                  <span className="mt-0.5 block font-mono text-[10px] text-muted-foreground">
                    {event.event_id.slice(0, 12)} · {event.payload_id ? "Payload 可用" : "无 Payload"}
                  </span>
                </button>
                <div className="flex items-start gap-1">
                  {event.payload_id ? (
                    <Button type="button" variant="ghost" size="icon" className="h-7 w-7" aria-label="查看 Payload" title="查看 Payload" onClick={() => onLoadPayload(event.payload_id!)}>
                      <Eye className="h-3.5 w-3.5" />
                    </Button>
                  ) : null}
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
              <p className="py-2 text-[10.5px] text-muted-foreground">当前 Graph 未提供原始 Event 导航信息。</p>
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
