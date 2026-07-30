import { Bot, CornerDownLeft, GitBranch, Unlink, type LucideIcon } from "lucide-react";
import { type NodeProps } from "@xyflow/react";

import type { AuditRunKind, TraceDisplayStatus } from "@/lib/audit-types";
import { auditStatusLabel } from "@/lib/audit-display";
import { cn } from "@/lib/utils";

export type RegionNodeData = {
  label: string;
  status: TraceDisplayStatus;
  terminalStatus: TraceDisplayStatus;
  healthStatus: TraceDisplayStatus;
  laneKind: AuditRunKind | null;
  count: number;
};

const LANE_ICONS: Record<AuditRunKind, LucideIcon> = {
  main: Bot,
  child_agent: GitBranch,
  continuation: CornerDownLeft,
  unknown: Unlink,
};

export function RegionNode({ data }: NodeProps) {
  const region = data as RegionNodeData;
  const Icon = region.laneKind ? LANE_ICONS[region.laneKind] : Bot;
  return (
    <div data-lane-kind={region.laneKind ?? undefined} className={cn(
      "h-full w-full rounded-lg border border-dashed bg-background/45",
      region.laneKind === "main" && "border-foreground/20 bg-muted/15",
      region.laneKind === "child_agent" && "border-teal-600/35 bg-teal-500/[0.035]",
      region.laneKind === "continuation" && "border-blue-500/40 bg-blue-500/[0.035]",
      region.laneKind === "unknown" && "border-amber-500/40 bg-amber-500/[0.035]",
    )}>
      <div className="flex h-9 items-center justify-between border-b border-border/45 px-3 text-[11px] font-medium text-muted-foreground">
        <span className="flex min-w-0 items-center gap-1.5">
          <Icon className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate">{region.label}</span>
        </span>
        <span className="shrink-0">
          {region.count} 节点 · 终态 {auditStatusLabel(region.terminalStatus)}
          {region.healthStatus !== region.terminalStatus
            ? ` · 过程 ${auditStatusLabel(region.healthStatus)}`
            : ""}
        </span>
      </div>
    </div>
  );
}
