import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionTraceList } from "@/components/traces/SessionTraceList";
import { TraceTimeline } from "@/components/traces/TraceTimeline";
import type { useAuditTimeline } from "@/hooks/useAuditTimeline";
import { fetchAuditGraph } from "@/lib/audit-api";
import type { AuditSessionListItem } from "@/lib/audit-types";

const timeline = {
  events: [],
  total: 37,
  nextCursor: null,
  loading: false,
  error: null,
  loadMore: () => null,
  refresh: () => null,
} as unknown as ReturnType<typeof useAuditTimeline>;

function TimelineHarness() {
  const [open, setOpen] = useState(false);
  return (
    <TraceTimeline
      timeline={timeline}
      total={37}
      open={open}
      selectedEventId={null}
      currentNodeIds={new Set()}
      onOpenChange={setOpen}
      onSelectEvent={vi.fn()}
      onLoadPayload={vi.fn()}
    />
  );
}

describe("audit trace UX", () => {
  beforeEach(() => {
    document.documentElement.lang = "zh-CN";
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the authoritative Event total before loading and supports maximize", () => {
    render(<TimelineHarness />);
    expect(screen.getByText(/Event 时间线 · 37/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Event 时间线/ }));
    expect(screen.getByRole("button", { name: "拖拽调整时间线高度" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "最大化时间线" }));
    expect(screen.getByRole("button", { name: "还原时间线高度" })).toBeInTheDocument();
  });

  it("requests the unified full Trace graph by default", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      level: "trace_full",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await fetchAuditGraph("token", "trace-1", null);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/audit/traces/trace-1/graph?level=trace_full",
      expect.objectContaining({ credentials: "same-origin" }),
    );
  });

  it("expands a one-Trace Session and loads its Trace from the backend", async () => {
    const session: AuditSessionListItem = {
      session_key: "websocket:chat-1",
      title: "会话标题",
      source_types: ["websocket"],
      first_seen: "2026-01-01T00:00:00Z",
      last_seen: "2026-01-01T00:00:01Z",
      trace_count: 1,
      active_trace_count: 0,
      warning_count: 0,
      error_count: 0,
      integrity_status: "valid",
      latest_trace_id: "trace-1",
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      items: [{
        trace_id: "trace-1",
        title: "会话标题",
        source_types: ["websocket"],
        primary_source_type: "websocket",
        first_seen: "2026-01-01T00:00:00Z",
        last_seen: "2026-01-01T00:00:01Z",
        display_status: "succeeded",
        turn_count: 1,
        run_count: 1,
        anomaly_count: 0,
        integrity_status: "valid",
        active: false,
        session_key: "websocket:chat-1",
        event_count: 12,
      }],
      next_cursor: null,
      index: { state: "ready", revision: 1, coverage_complete: true },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    render(
      <SessionTraceList
        token="token"
        sessions={[session]}
        index={null}
        query=""
        selectedTraceId={null}
        selectedSessionKey={null}
        loading={false}
        loadingMore={false}
        hasMore={false}
        onQueryChange={vi.fn()}
        onSelectTrace={vi.fn()}
        onLoadMore={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /会话标题/ })).toHaveAttribute("aria-expanded", "true");
    await waitFor(() => expect(screen.getByText(/12 Event/)).toBeInTheDocument());
  });
});
