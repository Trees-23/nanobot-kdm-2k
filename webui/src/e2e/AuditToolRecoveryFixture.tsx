import { useState } from "react";
import ReactDOM from "react-dom/client";

import { TraceGraph } from "@/components/traces/TraceGraph";
import type { AuditGraphEdge, AuditGraphResponse } from "@/lib/audit-types";
import "@/globals.css";

const graph: AuditGraphResponse = {
  trace: {
    trace_id: "trace-recovery-fixture",
    title: "Tool recovery fixture",
    display_status: "warning",
    first_seen: "2026-08-01T00:00:00Z",
    last_seen: "2026-08-01T00:00:02Z",
    session_key: "fixture:recovery",
    source_types: ["fixture"],
    active: false,
    event_count: 4,
  },
  level: "trace_full",
  focus: { turn_id: null, run_id: null },
  regions: [{ id: "turn:fixture", type: "turn", label: "Recovery fixture", status: "warning", parent_region_id: null, member_node_ids: ["failed", "recovered"], order: 0 }],
  nodes: [
    { id: "failed", type: "tool_call", status: "failed", label: "read_file failed", started_at: "2026-08-01T00:00:00Z", finished_at: "2026-08-01T00:00:01Z", elapsed_ms: 1_000, raw_event_ids: ["failed-finished"], region_id: "turn:fixture", parent_node_id: null, child_node_ids: [], expandable: false, relations: [], summary: { kind: "tool_call", tool_name: "read_file", error_summary: "File not found", error_type: "FileNotFoundError" }, order: 0 },
    { id: "recovered", type: "tool_call", status: "succeeded", label: "read_file recovered", started_at: "2026-08-01T00:00:01Z", finished_at: "2026-08-01T00:00:02Z", elapsed_ms: 1_000, raw_event_ids: ["recovered-finished"], region_id: "turn:fixture", parent_node_id: null, child_node_ids: [], expandable: false, relations: [], summary: { kind: "tool_call", tool_name: "read_file" }, order: 1 },
  ],
  edges: [{ id: "recovery-edge", type: "tool_recovery", relation: "tool_recovery", source: "failed", target: "recovered", anchor: { source_event_id: "failed-finished", target_event_id: "recovered-finished" } }],
  first_anomaly: { node_id: "failed", event_id: "failed-finished", category: "tool_finished", rule: "earliest_qualifying_event" },
  collapse_groups: [], expansion_groups: [], ignored_event_ids: [], integrity: { status: "valid", error_codes: [], warning_codes: [] }, index: { revision: 1, coverage_complete: true, lag_ms: 0 },
};

function Fixture() {
  const [selectedEdge, setSelectedEdge] = useState<AuditGraphEdge | null>(null);
  const [focusMode, setFocusMode] = useState<"recovery" | null>("recovery");
  const [located, setLocated] = useState<string[]>([]);
  const locate = (eventId: string) => setLocated((current) => [...new Set([...current, eventId])]);
  return (
    <main className="flex h-dvh min-h-0 flex-col bg-background text-foreground">
      <header className="flex h-12 shrink-0 items-center border-b px-4 text-sm font-medium">Tool recovery Chromium fixture</header>
      <section className="relative min-h-0 flex-1">
        <TraceGraph graph={graph} selectedNodeId="failed" focusMode={focusMode} onSelectNode={() => undefined} onFocusMode={(mode) => setFocusMode(mode === "recovery" ? mode : null)} onSelectEdge={setSelectedEdge} />
        {selectedEdge ? <aside className="absolute right-3 top-3 z-20 w-80 rounded-md border bg-background p-3 text-xs shadow-lg" aria-label="恢复关系检查器">
          <h2 className="font-semibold">Tool 恢复关系</h2>
          <p className="mt-2">失败端：failed · failed</p><p>恢复端：recovered · succeeded</p><p>显式 recovery 证据计数：1</p>
          <button className="mt-2 block underline" onClick={() => locate(selectedEdge.anchor?.source_event_id ?? "")}>定位失败端 Event</button>
          <button className="mt-1 block underline" onClick={() => locate(selectedEdge.anchor?.target_event_id ?? "")}>定位恢复端 Event</button>
        </aside> : null}
        <output className="absolute bottom-3 left-3 z-20 rounded border bg-background/95 px-2 py-1 text-[11px]" data-testid="located-events">{located.join(",") || "未定位 Event"}</output>
      </section>
    </main>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("root element missing");
ReactDOM.createRoot(root).render(<Fixture />);
