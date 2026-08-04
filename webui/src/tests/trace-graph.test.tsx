import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { edgeHandles, TraceGraph } from "@/components/traces/TraceGraph";
import type { AuditGraphResponse } from "@/lib/audit-types";

function graphFixture(): AuditGraphResponse {
  return {
    trace: {
      trace_id: "trace-1",
      title: "Failed tool run",
      display_status: "failed",
      first_seen: "2026-07-28T10:00:00Z",
      last_seen: "2026-07-28T10:00:02Z",
      session_key: "websocket:chat-1",
      source_types: ["websocket"],
      active: false,
      event_count: 2,
    },
    level: "trace",
    focus: { turn_id: null, run_id: null },
    regions: [{
      id: "turn:1",
      type: "turn",
      label: "Turn 1",
      status: "failed",
      parent_region_id: null,
      member_node_ids: ["run:1"],
      order: 0,
    }],
    nodes: [{
      id: "run:1",
      type: "run",
      status: "failed",
      label: "Main run",
      started_at: "2026-07-28T10:00:00Z",
      finished_at: "2026-07-28T10:00:02Z",
      elapsed_ms: 2_000,
      raw_event_ids: ["e1", "e2"],
      region_id: "turn:1",
      parent_node_id: null,
      child_node_ids: [],
      expandable: true,
      relations: [],
      summary: {
        kind: "run",
        actor_type: "main",
        iteration_count: 1,
        model_call_count: 1,
        tool_call_count: 1,
        identifier: "run-1",
      },
      order: 0,
    }],
    edges: [],
    first_anomaly: {
      node_id: "run:1",
      event_id: "e2",
      category: "tool_finished",
      rule: "earliest_qualifying_event",
    },
    collapse_groups: [],
    expansion_groups: [],
    ignored_event_ids: [],
    integrity: { status: "valid", error_codes: [], warning_codes: [] },
    index: { revision: 1, coverage_complete: true, lag_ms: 10 },
  };
}

describe("TraceGraph", () => {
  beforeEach(() => {
    document.documentElement.lang = "zh-CN";
  });
  it("renders selectable stable nodes without a Run drill interaction", async () => {
    const onSelectNode = vi.fn();
    render(
      <div style={{ width: 900, height: 700 }}>
        <TraceGraph
          graph={graphFixture()}
          selectedNodeId={null}
          focusMode={null}
          onSelectNode={onSelectNode}
          onFocusMode={vi.fn()}
        />
      </div>,
    );

    const node = await screen.findByLabelText(/run 失败 Main run 2\.0s/i);
    fireEvent.click(node);
    expect(onSelectNode).toHaveBeenCalledWith("run:1");
    fireEvent.keyDown(node.closest(".react-flow__node")!, { key: "Enter" });
    expect(onSelectNode).toHaveBeenLastCalledWith("run:1");
    expect(screen.queryByRole("button", { name: "下钻运行" })).not.toBeInTheDocument();
  });

  it("keeps geometry and viewport transform stable when selection changes", async () => {
    const graph = graphFixture();
    const { container, rerender } = render(
      <div style={{ width: 900, height: 700 }}>
        <TraceGraph graph={graph} selectedNodeId={null} focusMode={null} onSelectNode={vi.fn()} onFocusMode={vi.fn()} />
      </div>,
    );
    await screen.findByLabelText(/run 失败 Main run 2\.0s/i);
    const viewport = container.querySelector<HTMLElement>(".react-flow__viewport")!;
    await waitFor(() => expect(viewport.style.transform).not.toBe(""));
    const transformBefore = viewport.style.transform;
    const geometryBefore = screen.getByTestId("trace-graph").dataset.geometryKey;

    rerender(
      <div style={{ width: 900, height: 700 }}>
        <TraceGraph
          graph={structuredClone(graph)}
          selectedNodeId="run:1"
          focusMode={null}
          onSelectNode={vi.fn()}
          onFocusMode={vi.fn()}
        />
      </div>,
    );

    expect(screen.getByTestId("trace-graph").dataset.geometryKey).toBe(geometryBefore);
    expect(container.querySelector<HTMLElement>(".react-flow__viewport")!.style.transform).toBe(transformBefore);
  });

  it("collapses backend-declared successful chains by default and allows expansion", async () => {
    const graph = graphFixture();
    const operations = [1, 2, 3].map((order) => ({
      ...graph.nodes[0],
      id: `tool:${order}`,
      type: "tool_call" as const,
      status: "succeeded" as const,
      label: `Operation ${order}`,
      region_id: "turn:1",
      raw_event_ids: [`event-${order}`],
      summary: { kind: "tool_call" as const, tool_name: `operation_${order}` },
      order,
    }));
    graph.nodes.push(...operations);
    graph.regions[0].member_node_ids.push(...operations.map((node) => node.id));
    graph.edges.push(
      { id: "sequence-1", type: "sequence", source: operations[0].id, target: operations[1].id },
      { id: "sequence-2", type: "sequence", source: operations[1].id, target: operations[2].id },
    );
    graph.collapse_groups.push({
      id: "success-chain",
      member_node_ids: operations.map((node) => node.id),
      status: "succeeded",
      label: "3 successful operations",
      elapsed_ms: 30,
    });

    render(
      <div style={{ width: 900, height: 700 }}>
        <TraceGraph graph={graph} selectedNodeId={null} focusMode={null} onSelectNode={vi.fn()} onFocusMode={vi.fn()} />
      </div>,
    );

    const expand = await screen.findByRole("button", { name: "展开 3 successful operations" });
    expect(screen.queryByText("Operation 1")).not.toBeInTheDocument();
    fireEvent.click(expand);
    expect(await screen.findByText("Operation 1")).toBeInTheDocument();
  });

  it("names the model attempt control for its next action", async () => {
    const graph = graphFixture();
    const model = {
      ...graph.nodes[0],
      id: "model:1",
      type: "model_call" as const,
      status: "succeeded" as const,
      label: "Model call",
      expandable: true,
      summary: { kind: "model_call" as const, provider: "openai", model: "gpt-test" },
      order: 1,
    };
    const attempt = {
      ...model,
      id: "attempt:1",
      type: "model_attempt" as const,
      label: "Attempt 1",
      expandable: false,
      summary: { kind: "model_attempt" as const, provider: "openai", model: "gpt-test" },
      order: 2,
    };
    graph.nodes.push(model, attempt);
    graph.regions[0].member_node_ids.push(model.id, attempt.id);
    graph.expansion_groups.push({
      id: "attempts:model:1",
      owner_node_id: model.id,
      member_node_ids: [attempt.id],
      default_expanded: false,
    });

    render(
      <div style={{ width: 900, height: 700 }}>
        <TraceGraph graph={graph} selectedNodeId={null} focusMode={null} onSelectNode={vi.fn()} onFocusMode={vi.fn()} />
      </div>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "展开模型尝试" }));
    expect(await screen.findByRole("button", { name: "收起模型尝试" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "收起模型尝试" }));
    expect(await screen.findByRole("button", { name: "展开模型尝试" })).toBeInTheDocument();
  });

  it("locates the backend-declared first anomaly", async () => {
    const onSelectNode = vi.fn();
    const onFocusMode = vi.fn();
    render(
      <div style={{ width: 900, height: 700 }}>
        <TraceGraph
          graph={graphFixture()}
          selectedNodeId={null}
          focusMode={null}
          onSelectNode={onSelectNode}
          onFocusMode={onFocusMode}
        />
      </div>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "定位首个异常" }));
    expect(onSelectNode).toHaveBeenCalledWith("run:1");
    expect(onFocusMode).toHaveBeenCalledWith("causal");
  });

  it("opens a keyboard-accessible legend and renames main Run location", async () => {
    const onSelectNode = vi.fn();
    render(
      <div style={{ width: 900, height: 700 }}>
        <TraceGraph
          graph={graphFixture()}
          selectedNodeId={null}
          focusMode={null}
          onSelectNode={onSelectNode}
          onFocusMode={vi.fn()}
        />
      </div>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "图例" }));
    expect(screen.getByRole("dialog", { name: "运行轨迹图例" })).toHaveFocus();
    expect(screen.getByText(/箭头从原因/)).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "运行轨迹图例" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "定位 main Run" }));
    expect(onSelectNode).toHaveBeenCalledWith("run:1");
    expect(screen.queryByRole("button", { name: "定位主轴" })).not.toBeInTheDocument();
  });

  it("reports zero relation hits without counting the selected node", async () => {
    const onFocusMode = vi.fn();
    render(
      <div style={{ width: 900, height: 700 }}>
        <TraceGraph
          graph={graphFixture()}
          selectedNodeId="run:1"
          focusMode="causal"
          onSelectNode={vi.fn()}
          onFocusMode={onFocusMode}
        />
      </div>,
    );

    expect(await screen.findByText("因果链：零命中")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清除" }));
    expect(onFocusMode).toHaveBeenCalledWith(null);
  });

  it("reports deterministic node and edge counts for a relation focus", async () => {
    const graph = graphFixture();
    graph.nodes.push({
      ...graph.nodes[0],
      id: "tool:1",
      type: "tool_call",
      label: "Read config",
      parent_node_id: "run:1",
      summary: { kind: "tool_call", tool_name: "read_file" },
      order: 1,
    });
    graph.edges.push({
      id: "edge:caused-by",
      type: "caused_by",
      source: "run:1",
      target: "tool:1",
    });
    render(
      <div style={{ width: 900, height: 700 }}>
        <TraceGraph
          graph={graph}
          selectedNodeId="run:1"
          focusMode="causal"
          onSelectNode={vi.fn()}
          onFocusMode={vi.fn()}
        />
      </div>,
    );

    expect(await screen.findByText("因果链：2 个节点 / 1 条边")).toBeInTheDocument();
  });

  it("focuses explicit tool relations without adding them to causal focus", async () => {
    const graph = graphFixture();
    graph.nodes.push({
      ...graph.nodes[0],
      id: "tool:recovered",
      type: "tool_call",
      label: "Read corrected config",
      parent_node_id: "run:1",
      summary: { kind: "tool_call", tool_name: "read_file" },
      order: 1,
    });
    graph.edges.push({
      id: "tool_recovery:failed:recovered",
      type: "tool_recovery",
      source: "run:1",
      target: "tool:recovered",
      anchor: { source_event_id: "failed-event", target_event_id: "recovered-event" },
    });
    graph.edges.push({
      id: "tool_retry:failed:recovered",
      type: "tool_retry",
      source: "run:1",
      target: "tool:recovered",
      anchor: { source_event_id: "failed-event", target_event_id: "retry-event" },
    });
    graph.edges.push({
      id: "tool_continuation:failed:recovered",
      type: "tool_continuation",
      source: "run:1",
      target: "tool:recovered",
      anchor: { source_event_id: "failed-event", target_event_id: "continued-event" },
    });
    graph.edges.push({
      id: "sequence:failed:recovered",
      type: "sequence",
      source: "run:1",
      target: "tool:recovered",
    });
    const { rerender } = render(
      <div style={{ width: 900, height: 700 }}>
        <TraceGraph graph={graph} selectedNodeId="run:1" focusMode="recovery" onSelectNode={vi.fn()} onFocusMode={vi.fn()} />
      </div>,
    );

    expect(await screen.findByText("恢复关系：2 个节点 / 1 条边")).toBeInTheDocument();
    const recovery = graph.edges.find((edge) => edge.id === "tool_recovery:failed:recovered")!;
    const sequence = graph.edges.find((edge) => edge.id === "sequence:failed:recovered")!;
    expect(edgeHandles(recovery, graph)).toEqual({ sourceHandle: "right-recovery-source", targetHandle: "left-recovery-target" });
    expect(edgeHandles(graph.edges.find((edge) => edge.id === "tool_retry:failed:recovered")!, graph)).toEqual({
      sourceHandle: "right-recovery-source",
      targetHandle: "left-recovery-target",
    });
    expect(edgeHandles(graph.edges.find((edge) => edge.id === "tool_continuation:failed:recovered")!, graph)).toEqual({
      sourceHandle: "bottom-structure-source",
      targetHandle: "top-structure-target",
    });
    expect(edgeHandles(sequence, graph)).toEqual({ sourceHandle: "bottom-sequence-source", targetHandle: "top-sequence-target" });
    const routeMetadata = JSON.parse(screen.getByTestId("trace-graph").dataset.relationRoutes ?? "[]") as Array<{
      edgeId: string;
      bends: number;
      routeLength: number;
      detourRatio: number;
    }>;
    expect(routeMetadata.map((route) => route.edgeId).sort()).toEqual([
      "sequence:failed:recovered",
      "tool_continuation:failed:recovered",
      "tool_recovery:failed:recovered",
      "tool_retry:failed:recovered",
    ]);
    expect(routeMetadata.every((route) => route.bends >= 0)).toBe(true);
    expect(routeMetadata.every((route) => route.routeLength > 0)).toBe(true);
    expect(routeMetadata.every((route) => route.detourRatio >= 1)).toBe(true);
    rerender(
      <div style={{ width: 900, height: 700 }}>
        <TraceGraph graph={graph} selectedNodeId="run:1" focusMode="causal" onSelectNode={vi.fn()} onFocusMode={vi.fn()} />
      </div>,
    );
    expect(await screen.findByText("因果链：零命中")).toBeInTheDocument();
  });

  it("shows an explicit empty state for recovery focus", async () => {
    render(
      <div style={{ width: 900, height: 700 }}>
        <TraceGraph graph={graphFixture()} selectedNodeId="run:1" focusMode="recovery" onSelectNode={vi.fn()} onFocusMode={vi.fn()} />
      </div>,
    );

    expect(await screen.findByText("恢复关系：0 个节点 / 0 条边")).toBeInTheDocument();
  });

  it("focuses result return without classifying it as recovery", async () => {
    const graph = graphFixture();
    graph.nodes.push({ ...graph.nodes[0], id: "run:continuation", label: "Continuation", order: 1 });
    graph.edges.push({ id: "result", type: "result_return", source: "run:1", target: "run:continuation" });

    render(
      <div style={{ width: 900, height: 700 }}>
        <TraceGraph graph={graph} selectedNodeId="run:1" focusMode="result" onSelectNode={vi.fn()} onFocusMode={vi.fn()} />
      </div>,
    );

    expect(await screen.findByText("结果回传：2 个节点 / 1 条边")).toBeInTheDocument();
  });

  it("keeps secondary relations out of the default structure and reveals one from the relation list", async () => {
    const graph = graphFixture();
    const onSelectEdge = vi.fn();
    graph.nodes.push({ ...graph.nodes[0], id: "task:1", type: "task", label: "Inspect metadata", order: 1 });
    graph.nodes.push({ ...graph.nodes[0], id: "run:child", label: "Child run", order: 2, lane_order: 1, lane_side: "right" });
    graph.edges.push(
      { id: "spawn", type: "spawn_branch", source: "run:1", target: "task:1" },
      { id: "execution", type: "task_execution", source: "task:1", target: "run:child" },
      { id: "result", type: "result_return", source: "run:child", target: "run:1" },
    );
    graph.regions[0].member_node_ids.push("task:1", "run:child");

    render(
      <div style={{ width: 900, height: 700 }}>
        <TraceGraph graph={graph} selectedNodeId={null} focusMode={null} onSelectNode={vi.fn()} onFocusMode={vi.fn()} onSelectEdge={onSelectEdge} />
      </div>,
    );

    await screen.findByText("Inspect metadata");
    expect(screen.getByTestId("trace-graph").dataset.relationRoutes).toContain("spawn");
    expect(screen.getByTestId("trace-graph").dataset.relationRoutes).not.toContain('"result"');
    fireEvent.click(screen.getByRole("button", { name: "结果回传" }));
    await waitFor(() => expect(screen.getByTestId("trace-graph").dataset.relationRoutes).toContain('"result"'));
    expect(onSelectEdge).toHaveBeenCalledWith(expect.objectContaining({ id: "result" }));
  });

  it("shows every result return by default for a multi-agent trace", async () => {
    const graph = graphFixture();
    const resultSources = ["task:1", "task:2", "task:3"];
    const resultTargets = ["decision:1", "decision:2", "decision:3"];
    graph.nodes.push(
      ...resultSources.map((id, index) => ({
        ...graph.nodes[0],
        id,
        type: "task" as const,
        label: `Child task ${index + 1}`,
        order: index + 1,
      })),
      ...resultTargets.map((id, index) => ({
        ...graph.nodes[0],
        id,
        type: "checkpoint" as const,
        label: `Result checkpoint ${index + 1}`,
        order: index + 4,
      })),
    );
    graph.regions[0].member_node_ids.push(...resultSources, ...resultTargets);
    graph.edges.push(
      ...resultSources.map((source, index) => ({
        id: `result-${index + 1}`,
        type: "result_return" as const,
        source,
        target: resultTargets[index],
      })),
    );

    render(
      <div style={{ width: 900, height: 700 }}>
        <TraceGraph graph={graph} selectedNodeId={null} focusMode={null} onSelectNode={vi.fn()} onFocusMode={vi.fn()} />
      </div>,
    );

    await screen.findByText("Child task 1");
    await waitFor(() => {
      const routes = screen.getByTestId("trace-graph").dataset.relationRoutes ?? "";
      expect(routes).toContain('"result-1"');
      expect(routes).toContain('"result-2"');
      expect(routes).toContain('"result-3"');
    });
  });
});
