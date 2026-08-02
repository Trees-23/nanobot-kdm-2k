import ReactDOM from "react-dom/client";
import { useCallback, useState } from "react";

import { TraceTimeline } from "@/components/traces/TraceTimeline";
import type { useAuditTimeline } from "@/hooks/useAuditTimeline";
import type { AuditEventItem } from "@/lib/audit-types";
import "@/globals.css";

const TOTAL = 536;
const PAGE_SIZE = 200;

function makeEvent(index: number): AuditEventItem {
  return {
    event_id: `event-${String(index).padStart(3, "0")}`,
    event_type: index % 17 === 0 ? "ToolFinished" : "ModelOutput",
    occurred_at: new Date(Date.UTC(2026, 6, 30, 8, 0, index)).toISOString(),
    process_instance_id: "process-e2e",
    durability_epoch: 1,
    segment_id: "segment-e2e",
    segment_sequence: index,
    trace_id: "trace-e2e",
    turn_id: "turn-e2e",
    run_id: "run-main-e2e",
    model_call_id: null,
    attempt_id: null,
    tool_call_id: null,
    iteration: null,
    caused_by_event_id: null,
    status: "succeeded",
    elapsed_ms: index,
    payload_id: index % 23 === 0 ? `payload-${index}` : null,
    semantic_node_id: null,
    summary: `Event ${index}`,
  };
}

const ALL_EVENTS = Array.from({ length: TOTAL }, (_, index) => makeEvent(index + 1));

function appendUnique(current: AuditEventItem[], incoming: AuditEventItem[]) {
  const known = new Set(current.map((event) => event.event_id));
  return [...current, ...incoming.filter((event) => !known.has(event.event_id))];
}

function Fixture() {
  const [events, setEvents] = useState(() => ALL_EVENTS.slice(0, PAGE_SIZE));
  const [loading, setLoading] = useState(false);

  const loadMore = useCallback(async () => {
    if (loading || events.length >= TOTAL) return;
    setLoading(true);
    await new Promise((resolve) => window.setTimeout(resolve, 30));
    const next = ALL_EVENTS.slice(
      Math.max(0, events.length - 1),
      Math.min(TOTAL, events.length + PAGE_SIZE),
    );
    setEvents((current) => appendUnique(current, next));
    setLoading(false);
  }, [events.length, loading]);

  const timeline = {
    events,
    total: TOTAL,
    revision: 1,
    nextCursor: events.length < TOTAL ? `cursor-${events.length}` : null,
    loading,
    error: null,
    loadMore,
    refresh: async () => undefined,
    ensureEvent: async () => "not_found" as const,
  } as ReturnType<typeof useAuditTimeline>;

  return (
    <main className="flex h-dvh min-h-0 flex-col overflow-hidden bg-background text-foreground">
      <header className="flex h-12 shrink-0 items-center border-b px-4 text-sm font-medium">
        Audit Trace 536 Event 浏览器夹具
      </header>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="min-h-0 flex-1 bg-muted/20" data-testid="audit-workbench-space" />
        <TraceTimeline
          timeline={timeline}
          total={TOTAL}
          open
          selectedEventId={null}
          currentNodeIds={new Set()}
          onOpenChange={() => undefined}
          onSelectEvent={() => undefined}
          onLoadPayload={() => undefined}
        />
      </div>
      <output
        className="sr-only"
        data-testid="fixture-state"
        data-loaded-count={events.length}
        data-next-cursor={timeline.nextCursor ?? ""}
      />
    </main>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("root element missing");
ReactDOM.createRoot(root).render(<Fixture />);
