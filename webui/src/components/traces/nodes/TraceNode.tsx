import { memo } from "react";
import {
  AlertTriangle,
  Bot,
  Box,
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Clock3,
  Flag,
  GitBranch,
  LoaderCircle,
  ListTodo,
  PauseCircle,
  RefreshCcw,
  RotateCcw,
  ShieldAlert,
  TerminalSquare,
  Wrench,
  XCircle,
} from "lucide-react";
import { Handle, Position, type NodeProps } from "@xyflow/react";

import type { AuditGraphNode, AuditNodeType, TraceDisplayStatus } from "@/lib/audit-types";
import { auditNodeTypeLabel, auditStatusLabel, auditValueLabel } from "@/lib/audit-display";
import { cn } from "@/lib/utils";

export type TraceNodeData = {
  node: AuditGraphNode;
  selected: boolean;
  dimmed: boolean;
  expanded: boolean;
  onExpand: (node: AuditGraphNode) => void;
};

const TYPE_ICONS: Record<AuditNodeType, typeof Bot> = {
  run: Bot,
  task: ListTodo,
  model_call: BrainCircuit,
  model_attempt: RefreshCcw,
  tool_call: Wrench,
  decision: GitBranch,
  checkpoint: Box,
  goal: Flag,
  turn_result: CheckCircle2,
  delivery: TerminalSquare,
  anomaly: AlertTriangle,
  external_reference: ShieldAlert,
};

function statusIcon(status: TraceDisplayStatus) {
  if (status === "succeeded") return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />;
  if (status === "failed") return <XCircle className="h-3.5 w-3.5 text-destructive" />;
  if (status === "running") return <LoaderCircle className="h-3.5 w-3.5 animate-spin text-orange-500 motion-reduce:animate-none" />;
  if (status === "warning") return <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />;
  if (status === "interrupted") return <PauseCircle className="h-3.5 w-3.5 text-blue-600" />;
  if (status === "cancelled") return <Clock3 className="h-3.5 w-3.5 text-muted-foreground" />;
  return <CircleDashed className="h-3.5 w-3.5 text-muted-foreground" />;
}

function meta(node: AuditGraphNode): string {
  if (node.type === "run") {
    return `${node.summary.iteration_count ?? 0} iter · ${node.summary.model_call_count ?? 0} model · ${node.summary.tool_call_count ?? 0} tool`;
  }
  if (node.type === "model_call" || node.type === "model_attempt") {
    return [node.summary.provider, node.summary.model].filter(Boolean).join(" / ") || "model";
  }
  if (node.type === "tool_call") return node.summary.tool_name ?? "tool";
  return node.summary.decision_type
    ? auditValueLabel(node.summary.decision_type)
    : node.summary.subtype
      ? auditValueLabel(node.summary.subtype)
      : auditNodeTypeLabel(node.type);
}

function formatElapsed(value: number | null): string {
  if (value == null) return "-";
  if (value < 1_000) return `${value}ms`;
  return `${(value / 1_000).toFixed(value < 10_000 ? 1 : 0)}s`;
}

function TraceNodeComponent({ data }: NodeProps) {
  const value = data as TraceNodeData;
  const node = value.node;
  const Icon = TYPE_ICONS[node.type];
  const terminalStatus = node.terminal_status ?? node.status;
  const healthStatus = node.health_status ?? terminalStatus;
  const runKindLabel = node.run_kind === "child_agent"
    ? "Child"
    : node.run_kind === "continuation"
      ? "Continuation"
      : node.run_kind === "unknown"
        ? "Unlinked"
        : node.run_kind === "main"
          ? "Main"
          : null;
  return (
    <div
      data-run-kind={node.run_kind ?? undefined}
      data-lane-kind={node.lane_kind ?? undefined}
      data-terminal-status={terminalStatus}
      data-health-status={healthStatus}
      className={cn(
        "relative flex h-[76px] w-[248px] flex-col justify-between overflow-hidden rounded-lg border bg-card px-3 py-2 text-card-foreground",
        "shadow-[0_1px_2px_hsl(var(--foreground)/0.05)] transition-[border-color,box-shadow,opacity]",
        value.selected && "border-orange-500 ring-2 ring-orange-500/20 shadow-md",
        value.dimmed && "opacity-30",
      )}
      aria-label={`${auditNodeTypeLabel(node.type)} ${auditStatusLabel(terminalStatus)} ${node.label} ${formatElapsed(node.elapsed_ms)}`}
    >
      <span className={cn(
        "absolute inset-y-0 left-0 w-1",
        terminalStatus === "failed" && "bg-destructive",
        terminalStatus === "warning" && "bg-amber-500",
        terminalStatus === "running" && "bg-orange-500",
        terminalStatus === "succeeded" && "bg-emerald-500",
        !["failed", "warning", "running", "succeeded"].includes(terminalStatus) && "bg-muted-foreground/50",
      )} />
      <div className="flex min-w-0 items-center gap-2 pl-1">
        <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-[12.5px] font-semibold">{node.label}</span>
        <span className="inline-flex shrink-0 items-center gap-1 text-[10.5px] text-muted-foreground">
          {statusIcon(terminalStatus)}{auditStatusLabel(terminalStatus)}
        </span>
      </div>
      <div className="flex min-w-0 items-end justify-between gap-2 pl-1">
        <div className="min-w-0">
          <p className="truncate text-[10.5px] text-muted-foreground">
            {runKindLabel ? `${runKindLabel} · ` : ""}{meta(node)}
          </p>
          <p className="mt-0.5 font-mono text-[10px] text-muted-foreground/75">{formatElapsed(node.elapsed_ms)}</p>
        </div>
        {healthStatus !== terminalStatus ? (
          <span className="inline-flex shrink-0 items-center gap-1 rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[9.5px] text-amber-700 dark:text-amber-400">
            {statusIcon(healthStatus)}过程 {auditStatusLabel(healthStatus)}
          </span>
        ) : null}
        {node.expandable && node.type === "model_call" ? (
          <button
            type="button"
            className="nodrag nopan grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={value.expanded ? "收起模型尝试" : "展开模型尝试"}
            title={value.expanded ? "收起模型尝试" : "展开模型尝试"}
            onClick={(event) => {
              event.stopPropagation();
              value.onExpand(node);
            }}
          >
            {value.expanded
              ? <RotateCcw className="h-3.5 w-3.5" />
              : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
        ) : null}
      </div>
      <Handle id="top-sequence-target" type="target" position={Position.Top} style={{ left: "66%" }} className="!h-1.5 !w-1.5 !border-background !bg-muted-foreground" />
      <Handle id="bottom-sequence-source" type="source" position={Position.Bottom} style={{ left: "34%" }} className="!h-1.5 !w-1.5 !border-background !bg-muted-foreground" />
      <Handle id="top-structure-target" type="target" position={Position.Top} style={{ left: "66%" }} className="!h-1.5 !w-1.5 !border-background !bg-teal-700" />
      <Handle id="bottom-structure-source" type="source" position={Position.Bottom} style={{ left: "34%" }} className="!h-1.5 !w-1.5 !border-background !bg-teal-700" />
      {(["left", "right"] as const).flatMap((side) => [
        <Handle key={`${side}-structure-source`} id={`${side}-structure-source`} type="source" position={side === "left" ? Position.Left : Position.Right} style={{ top: "35%" }} className="!h-1.5 !w-1.5 !border-background !bg-teal-700" />,
        <Handle key={`${side}-structure-target`} id={`${side}-structure-target`} type="target" position={side === "left" ? Position.Left : Position.Right} style={{ top: "35%" }} className="!h-1.5 !w-1.5 !border-background !bg-teal-700" />,
        <Handle key={`${side}-result-source`} id={`${side}-result-source`} type="source" position={side === "left" ? Position.Left : Position.Right} style={{ top: "58%" }} className="!h-1.5 !w-1.5 !border-background !bg-blue-600" />,
        <Handle key={`${side}-result-target`} id={`${side}-result-target`} type="target" position={side === "left" ? Position.Left : Position.Right} style={{ top: "58%" }} className="!h-1.5 !w-1.5 !border-background !bg-blue-600" />,
        <Handle key={`${side}-recovery-source`} id={`${side}-recovery-source`} type="source" position={side === "left" ? Position.Left : Position.Right} style={{ top: "76%" }} className="!h-1.5 !w-1.5 !border-background !bg-amber-600" />,
        <Handle key={`${side}-recovery-target`} id={`${side}-recovery-target`} type="target" position={side === "left" ? Position.Left : Position.Right} style={{ top: "76%" }} className="!h-1.5 !w-1.5 !border-background !bg-amber-600" />,
      ])}
    </div>
  );
}

export const TraceNode = memo(TraceNodeComponent);
