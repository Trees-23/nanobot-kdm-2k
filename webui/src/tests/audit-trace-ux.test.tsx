import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionTraceList } from "@/components/traces/SessionTraceList";
import { PayloadViewer } from "@/components/traces/PayloadViewer";
import { TraceNodeInspector } from "@/components/traces/TraceNodeInspector";
import { TraceTimeline } from "@/components/traces/TraceTimeline";
import { AuditCaptureModeNotice, TraceEdgeInspector } from "@/components/traces/TraceWorkbench";
import { useAuditTimeline } from "@/hooks/useAuditTimeline";
import { AuditApiError, fetchAuditGraph } from "@/lib/audit-api";
import type { AuditGraphNode, AuditSessionListItem } from "@/lib/audit-types";

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

function SelectedTimelineHarness() {
  const events = Array.from({ length: 24 }, (_, index) => auditEvent(`event-${index}`, index));
  const selectedTimeline = {
    ...timeline,
    events,
    total: events.length,
  } as unknown as ReturnType<typeof useAuditTimeline>;
  return (
    <TraceTimeline
      timeline={selectedTimeline}
      total={events.length}
      open
      selectedEventId="event-20"
      currentNodeIds={new Set()}
      onOpenChange={vi.fn()}
      onSelectEvent={vi.fn()}
      onLoadPayload={vi.fn()}
    />
  );
}

function auditEvent(eventId: string, sequence: number) {
  return {
    event_id: eventId,
    event_type: "tool_finished",
    occurred_at: `2026-01-01T00:00:${String(sequence).padStart(2, "0")}Z`,
    process_instance_id: "p",
    durability_epoch: sequence,
    segment_id: "s",
    segment_sequence: sequence,
    trace_id: "trace-1",
    turn_id: "turn",
    run_id: "run",
    model_call_id: null,
    attempt_id: null,
    tool_call_id: "tool",
    iteration: 1,
    caused_by_event_id: null,
    status: "ok",
    elapsed_ms: 1,
    payload_id: null,
    semantic_node_id: null,
    summary: "finish",
  };
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

  it("renders a programmatically selected Event outside the initial virtual range", async () => {
    render(<SelectedTimelineHarness />);
    await waitFor(() => {
      expect(document.querySelector('[data-event-id="event-20"]')).toHaveClass(/bg-sidebar-accent/);
    });
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

  it("shows Tool root cause first and opens Payload directly from graph navigation", () => {
    const onLoadPayload = vi.fn();
    const node: AuditGraphNode = {
      id: "tool:trace:run:search",
      type: "tool_call",
      status: "failed",
      label: "web_search",
      started_at: "2026-01-01T00:00:00Z",
      finished_at: "2026-01-01T00:00:30Z",
      elapsed_ms: 30_000,
      raw_event_ids: ["event-1"],
      raw_events: [{
        event_id: "event-1",
        event_type: "tool_finished",
        occurred_at: "2026-01-01T00:00:30Z",
        status: "timeout",
        payload_id: "payload-1",
      }],
      region_id: "lane:child",
      parent_node_id: null,
      child_node_ids: [],
      expandable: false,
      relations: [],
      summary: {
        kind: "tool_call",
        tool_name: "web_search",
        provider: "should-not-render-as-generic-row",
        error_type: "TimeoutError",
        error_code: "web_search_timeout",
        error_summary: "DuckDuckGo search timed out after 30s",
        error_message: "Error: DuckDuckGo search timed out after 30s; request id omitted",
        error_source: "timeout",
        retryability: "retryable",
        effective_timeout_ms: 30_000,
        safe_input_summary: "query omitted; provider=duckduckgo",
        impact: "run_failed",
        recovery_status: "unrecovered",
      },
      order: 0,
    };

    render(
      <TraceNodeInspector
        node={node}
        focusMode={null}
        onFocusMode={vi.fn()}
        onClose={vi.fn()}
        onLocateEvent={vi.fn()}
        onLoadPayload={onLoadPayload}
      />,
    );

    expect(screen.getByText("根因")).toBeInTheDocument();
    expect(screen.getByText("Error: DuckDuckGo search timed out after 30s; request id omitted")).toBeInTheDocument();
    expect(screen.getByText("错误来源")).toBeInTheDocument();
    expect(screen.getByText("timeout")).toBeInTheDocument();
    expect(screen.getByText("可重试性")).toBeInTheDocument();
    expect(screen.getByText("retryable")).toBeInTheDocument();
    expect(screen.getByText(/导致 Run 失败/)).toBeInTheDocument();
    expect(screen.queryByText("Provider")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看 Payload" }));
    expect(onLoadPayload).toHaveBeenCalledWith("payload-1");
  });

  it("shows Task lifecycle dimensions separately from child Run", () => {
    const node: AuditGraphNode = {
      id: "task:trace-1:task-a",
      type: "task",
      status: "succeeded",
      label: "检查一级目录",
      started_at: "2026-01-01T00:00:00Z",
      finished_at: "2026-01-01T00:00:30Z",
      elapsed_ms: 30_000,
      raw_event_ids: ["task-event-1"],
      raw_events: [{
        event_id: "task-event-1",
        event_type: "subagent_result_delivered",
        occurred_at: "2026-01-01T00:00:30Z",
        status: null,
        payload_id: null,
      }],
      region_id: "task-region:trace-1:task-a",
      parent_node_id: null,
      child_node_ids: [],
      expandable: false,
      relations: [],
      task_id: "task-a",
      summary: {
        kind: "task",
        task_id: "task-a",
        task_label: "检查一级目录",
        task_revision: 8,
        task_status: "succeeded",
        task_phase: "finished",
        termination_state: "confirmed_stopped",
        delivery_phase: "delivered",
        required_task: true,
        lifecycle_event_count: 8,
        owner_run_id: "run-main",
        child_run_id: "run-child",
      },
      order: 0,
    };

    render(
      <TraceNodeInspector
        node={node}
        focusMode={null}
        onFocusMode={vi.fn()}
        onClose={vi.fn()}
        onLocateEvent={vi.fn()}
        onLoadPayload={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "检查一级目录" })).toBeInTheDocument();
    expect(screen.getByText("Task ID")).toBeInTheDocument();
    expect(screen.getByText("task-a")).toBeInTheDocument();
    expect(screen.getByText("执行阶段")).toBeInTheDocument();
    expect(screen.getByText("终止状态")).toBeInTheDocument();
    expect(screen.getByText("交付阶段")).toBeInTheDocument();
    expect(screen.getByText("run-child")).toBeInTheDocument();
  });

  it("renders honest Payload 404 and retryable 503 states", () => {
    const { rerender } = render(
      <PayloadViewer
        payload={null}
        loading={false}
        error={new AuditApiError(404, "payload_not_found", "not found")}
        onRetry={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Payload 未找到、已清理或已过期")).toBeInTheDocument();

    rerender(
      <PayloadViewer
        payload={null}
        loading={false}
        error={new AuditApiError(503, "audit_payload_lookup_timeout", "timeout", true)}
        onRetry={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("explains metadata-only capture without promising Payload in full mode", () => {
    const { rerender } = render(<AuditCaptureModeNotice mode="metadata_only" />);
    expect(screen.getByText("当前仅记录事件元数据，不保存 Payload")).toBeInTheDocument();

    rerender(<AuditCaptureModeNotice mode="full" />);
    expect(screen.queryByText("当前仅记录事件元数据，不保存 Payload")).not.toBeInTheDocument();
  });

  it("labels result return endpoints without failure or recovery wording", () => {
    const source: AuditGraphNode = {
      id: "task:1", type: "task", status: "succeeded", label: "检查一级目录",
      started_at: null, finished_at: null, elapsed_ms: null, raw_event_ids: [], region_id: "task-region:1",
      parent_node_id: null, child_node_ids: [], expandable: false, relations: [], summary: { kind: "task" }, order: 0,
    };
    const target = { ...source, id: "run:continuation", type: "run" as const, label: "Main continuation", summary: { kind: "run" as const } };
    render(
      <TraceEdgeInspector
        edge={{ id: "result", type: "result_return", source: source.id, target: target.id, anchor: { source_event_id: "result-event", target_event_id: "injection-event" } }}
        source={source}
        target={target}
        onClose={vi.fn()}
        onLocateEvent={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "结果回传" })).toBeInTheDocument();
    expect(screen.getByText("结果来源")).toBeInTheDocument();
    expect(screen.getByText("注入位置")).toBeInTheDocument();
    expect(screen.queryByText(/失败端|恢复关系/)).not.toBeInTheDocument();
  });

  it("loads missing Events within the bounded locator and de-duplicates pages", async () => {
    const pages = [
      {
        items: [{
          event_id: "event-1", event_type: "tool_started", occurred_at: "2026-01-01T00:00:00Z",
          process_instance_id: "p", durability_epoch: 1, segment_id: "s", segment_sequence: 1,
          trace_id: "trace-1", turn_id: "turn", run_id: "run", model_call_id: null,
          attempt_id: null, tool_call_id: "tool", iteration: 1, caused_by_event_id: null,
          status: null, elapsed_ms: null, payload_id: null, semantic_node_id: null, summary: "start",
        }],
        next_cursor: "cursor-1", total: 2, index: { revision: 1 },
      },
      {
        items: [
          {
            event_id: "event-1", event_type: "tool_started", occurred_at: "2026-01-01T00:00:00Z",
            process_instance_id: "p", durability_epoch: 1, segment_id: "s", segment_sequence: 1,
            trace_id: "trace-1", turn_id: "turn", run_id: "run", model_call_id: null,
            attempt_id: null, tool_call_id: "tool", iteration: 1, caused_by_event_id: null,
            status: null, elapsed_ms: null, payload_id: null, semantic_node_id: null, summary: "start",
          },
          {
            event_id: "event-2", event_type: "tool_finished", occurred_at: "2026-01-01T00:00:01Z",
            process_instance_id: "p", durability_epoch: 2, segment_id: "s", segment_sequence: 2,
            trace_id: "trace-1", turn_id: "turn", run_id: "run", model_call_id: null,
            attempt_id: null, tool_call_id: "tool", iteration: 1, caused_by_event_id: null,
            status: "ok", elapsed_ms: 1, payload_id: null, semantic_node_id: null, summary: "finish",
          },
        ],
        next_cursor: null, total: 2, index: { revision: 1 },
      },
    ];
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify(pages[0]), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(pages[1]), { status: 200 }));
    const { result } = renderHook(() => useAuditTimeline("token", "trace-1", true));
    await waitFor(() => expect(result.current.events).toHaveLength(1));

    await act(async () => {
      expect(await result.current.ensureEvent("event-2")).toBe("found");
    });
    expect(result.current.events.map((event) => event.event_id)).toEqual(["event-1", "event-2"]);
  });

  it("loads the first page when locating before the timeline has opened", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      items: [auditEvent("event-target", 1)],
      next_cursor: null,
      total: 1,
      index: { revision: 3 },
    }), { status: 200 }));
    const { result } = renderHook(() => useAuditTimeline("token", "trace-1", false));

    await act(async () => {
      expect(await result.current.ensureEvent("event-target")).toBe("found");
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(result.current.events.map((event) => event.event_id)).toEqual(["event-target"]);
    expect(result.current.revision).toBe(3);
  });

  it("stops missing Event lookup after five additional pages", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({
      items: [auditEvent("event-0", 0)],
      next_cursor: "cursor-0",
      total: 1_500,
      index: { revision: 1 },
    }), { status: 200 }));
    for (let page = 1; page <= 5; page += 1) {
      fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({
        items: [auditEvent(`event-${page}`, page)],
        next_cursor: `cursor-${page}`,
        total: 1_500,
        index: { revision: 1 },
      }), { status: 200 }));
    }
    const { result } = renderHook(() => useAuditTimeline("token", "trace-1", true));
    await waitFor(() => expect(result.current.events).toHaveLength(1));

    await act(async () => {
      expect(await result.current.ensureEvent("event-1499")).toBe("limit");
    });
    expect(fetchMock).toHaveBeenCalledTimes(6);
    expect(result.current.events).toHaveLength(6);
  });

  it("rejects Event pagination when the index revision changes", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({
        items: [auditEvent("event-1", 1)],
        next_cursor: "cursor-1",
        total: 2,
        index: { revision: 7 },
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        items: [auditEvent("event-2", 2)],
        next_cursor: null,
        total: 2,
        index: { revision: 8 },
      }), { status: 200 }));
    const { result } = renderHook(() => useAuditTimeline("token", "trace-1", true));
    await waitFor(() => expect(result.current.revision).toBe(7));

    await act(async () => {
      expect(await result.current.ensureEvent("event-2")).toBe("revision_mismatch");
    });
    expect(result.current.events.map((event) => event.event_id)).toEqual(["event-1"]);
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
