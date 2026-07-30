import { Layers3 } from "lucide-react";
import { type NodeProps } from "@xyflow/react";

import type { AuditGraphResponse } from "@/lib/audit-types";

type Group = AuditGraphResponse["collapse_groups"][number];
export type CollapseGroupNodeData = { group: Group; onExpand: (groupId: string) => void };

export function CollapseGroupNode({ data }: NodeProps) {
  const value = data as CollapseGroupNodeData;
  return (
    <button
      type="button"
      className="nodrag nopan flex h-[52px] w-[248px] items-center gap-2 rounded-lg border border-border bg-card px-3 text-left shadow-sm outline-none hover:border-foreground/30 focus-visible:ring-2 focus-visible:ring-ring"
      aria-label={`展开 ${value.group.label}`}
      onClick={() => value.onExpand(value.group.id)}
    >
      <Layers3 className="h-4 w-4 shrink-0 text-emerald-600" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12px] font-medium">{value.group.label}</span>
        <span className="block text-[10px] text-muted-foreground">点击展开 · {value.group.elapsed_ms ?? 0}ms</span>
      </span>
    </button>
  );
}
