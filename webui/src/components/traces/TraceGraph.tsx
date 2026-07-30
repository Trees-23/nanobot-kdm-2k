import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BaseEdge,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  getSmoothStepPath,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeTypes,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  AlertTriangle,
  Focus,
  LocateFixed,
  Map as MapIcon,
  Network,
} from "lucide-react";

import { RegionNode, type RegionNodeData } from "@/components/traces/nodes/RegionNode";
import { TraceNode, type TraceNodeData } from "@/components/traces/nodes/TraceNode";
import {
  CollapseGroupNode,
  type CollapseGroupNodeData,
} from "@/components/traces/nodes/CollapseGroupNode";
import { Button } from "@/components/ui/button";
import type { AuditGraphNode, AuditGraphResponse, TraceEdgeType } from "@/lib/audit-types";
import { auditNodeTypeLabel, auditStatusLabel } from "@/lib/audit-display";
import { cn } from "@/lib/utils";
import type { TraceFocusMode } from "@/components/traces/TraceNodeInspector";

const NODE_WIDTH = 248;
const NODE_HEIGHT = 76;
type SemanticRenderNode = Node<TraceNodeData, "traceNode">;
type RegionRenderNode = Node<RegionNodeData, "regionNode">;
type GroupRenderNode = Node<CollapseGroupNodeData, "collapseGroup">;
type RenderNode = SemanticRenderNode | RegionRenderNode | GroupRenderNode;

const nodeTypes: NodeTypes = {
  traceNode: TraceNode,
  regionNode: RegionNode,
  collapseGroup: CollapseGroupNode,
};

function AuditEdge(props: EdgeProps) {
  const type = props.data?.auditType as TraceEdgeType | undefined;
  const [path] = getSmoothStepPath(props);
  const styles: Record<TraceEdgeType, { stroke: string; dash?: string; width: number }> = {
    sequence: { stroke: "hsl(var(--muted-foreground) / .35)", width: 1 },
    spawn_branch: { stroke: "#0f766e", width: 2 },
    result_return: { stroke: "#2563eb", dash: "7 4", width: 1.8 },
    caused_by: { stroke: "hsl(var(--foreground) / .8)", width: 1.8 },
    retry: { stroke: "#d97706", dash: "3 3", width: 1.5 },
    parent_run: { stroke: "hsl(var(--foreground) / .55)", width: 1.4 },
    resumed_from: { stroke: "#3b82f6", dash: "6 4", width: 1.5 },
    retry_of: { stroke: "#d97706", dash: "3 3", width: 1.5 },
  };
  const style = styles[type ?? "sequence"];
  return (
    <BaseEdge
      path={path}
      markerStart={props.markerStart}
      markerEnd={props.markerEnd}
      interactionWidth={props.interactionWidth}
      style={{
        ...props.style,
        stroke: style.stroke,
        strokeWidth: style.width,
        strokeDasharray: style.dash,
      }}
    />
  );
}

const edgeTypes = { audit: AuditEdge };

function relatedIds(
  graph: AuditGraphResponse,
  selectedId: string | null,
  mode: TraceFocusMode,
): Set<string> {
  if (!selectedId || !mode) return new Set();
  const allowed: Record<Exclude<TraceFocusMode, null>, TraceEdgeType[]> = {
    causal: ["caused_by", "retry", "retry_of"],
    context: ["sequence"],
    branch: ["spawn_branch", "parent_run"],
    resume: ["result_return", "resumed_from"],
  };
  const result = new Set([selectedId]);
  let changed = true;
  while (changed) {
    changed = false;
    graph.edges.forEach((edge) => {
      if (!allowed[mode].includes(edge.type)) return;
      if (result.has(edge.source) || result.has(edge.target)) {
        if (!result.has(edge.source) || !result.has(edge.target)) changed = true;
        result.add(edge.source);
        result.add(edge.target);
      }
    });
  }
  return result;
}

function fallbackPositions(nodes: AuditGraphNode[]) {
  return nodes.map((node, index) => ({
    id: node.id,
    x: 960 + (node.lane_order ?? 0) * 356,
    y: index * 116 + 84,
  }));
}

export function TraceGraph({
  graph,
  selectedNodeId,
  focusMode,
  onSelectNode,
  onFocusMode,
}: {
  graph: AuditGraphResponse;
  selectedNodeId: string | null;
  focusMode: TraceFocusMode;
  onSelectNode: (nodeId: string | null) => void;
  onFocusMode: (mode: TraceFocusMode) => void;
}) {
  const [positions, setPositions] = useState<Array<{ id: string; x: number; y: number }>>([]);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    () => new Set(graph.expansion_groups.filter((group) => group.default_expanded).map((group) => group.id)),
  );
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(
    () => new Set(),
  );
  const flowRef = useRef<ReactFlowInstance<RenderNode, Edge> | null>(null);
  const requestId = useRef(0);

  const hiddenAttemptIds = useMemo(() => new Set(
    graph.expansion_groups
      .filter((group) => !expandedGroups.has(group.id))
      .flatMap((group) => group.member_node_ids),
  ), [expandedGroups, graph.expansion_groups]);
  const hiddenCollapsedIds = useMemo(() => new Set(
    graph.collapse_groups
      .filter((group) => collapsedGroups.has(group.id))
      .flatMap((group) => group.member_node_ids),
  ), [collapsedGroups, graph.collapse_groups]);
  const visibleSemantic = useMemo(
    () => graph.nodes.filter((node) => !hiddenAttemptIds.has(node.id) && !hiddenCollapsedIds.has(node.id)),
    [graph.nodes, hiddenAttemptIds, hiddenCollapsedIds],
  );
  const activeCollapseGroups = useMemo(
    () => graph.collapse_groups.filter((group) => collapsedGroups.has(group.id)),
    [collapsedGroups, graph.collapse_groups],
  );

  useEffect(() => {
    const id = ++requestId.current;
    if (typeof Worker === "undefined") {
      const fallback = fallbackPositions(visibleSemantic);
      setPositions([
        ...fallback,
        ...activeCollapseGroups.map((group, index) => ({ id: `group:${group.id}`, x: 80, y: (fallback.length + index) * 132 + 60 })),
      ]);
      return;
    }
    const worker = new Worker(new URL("../../workers/auditLayout.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (event: MessageEvent<{ id: number; positions?: typeof positions }>) => {
      if (event.data.id === id && event.data.positions) {
        setPositions(event.data.positions);
        window.requestAnimationFrame(() => {
          void flowRef.current?.fitView({ padding: 0.2, duration: 0 });
        });
      }
      worker.terminate();
    };
    worker.onerror = () => {
      setPositions(fallbackPositions(visibleSemantic));
      worker.terminate();
    };
    worker.postMessage({
      id,
      nodes: [
        ...visibleSemantic.map((node) => ({
          id: node.id,
          width: NODE_WIDTH,
          height: NODE_HEIGHT,
          laneId: node.lane_id ?? node.region_id,
          laneOrder: node.lane_order ?? 0,
          order: node.order,
          runKind: node.run_kind ?? "main",
        })),
        ...activeCollapseGroups.map((group) => ({
          id: `group:${group.id}`,
          width: NODE_WIDTH,
          height: 52,
          laneId: graph.nodes.find((node) => group.member_node_ids.includes(node.id))?.lane_id ?? "unscoped",
          laneOrder: graph.nodes.find((node) => group.member_node_ids.includes(node.id))?.lane_order ?? 0,
          order: graph.nodes.find((node) => group.member_node_ids.includes(node.id))?.order ?? 0,
          runKind: graph.nodes.find((node) => group.member_node_ids.includes(node.id))?.run_kind ?? "main",
        })),
      ],
      edges: graph.edges
        .filter((edge) => !hiddenAttemptIds.has(edge.source) && !hiddenAttemptIds.has(edge.target))
        .map((edge) => ({ ...edge, relation: edge.relation ?? edge.type })),
    });
    return () => worker.terminate();
  }, [activeCollapseGroups, graph.edges, graph.nodes, hiddenAttemptIds, visibleSemantic]);

  const highlighted = useMemo(
    () => relatedIds(graph, selectedNodeId, focusMode),
    [focusMode, graph, selectedNodeId],
  );

  const toggleExpand = useCallback((node: AuditGraphNode) => {
    const group = graph.expansion_groups.find((candidate) => candidate.owner_node_id === node.id);
    if (!group) return;
    setExpandedGroups((current) => {
      const next = new Set(current);
      if (next.has(group.id)) next.delete(group.id);
      else next.add(group.id);
      return next;
    });
  }, [graph.expansion_groups]);

  const renderNodes = useMemo<RenderNode[]>(() => {
    const byId = new Map(positions.map((position) => [position.id, position]));
    const semantic: SemanticRenderNode[] = visibleSemantic.map((node) => ({
      id: node.id,
      type: "traceNode",
      position: byId.get(node.id) ?? { x: 80, y: node.order * 132 + 60 },
      draggable: false,
      selectable: true,
      focusable: true,
      ariaLabel: `${auditNodeTypeLabel(node.type)} ${auditStatusLabel(node.status)} ${node.label}`,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      zIndex: 2,
      data: {
        node,
        selected: selectedNodeId === node.id,
        dimmed: highlighted.size > 0 && !highlighted.has(node.id),
        expanded: graph.expansion_groups.some((group) => group.owner_node_id === node.id && expandedGroups.has(group.id)),
        onExpand: toggleExpand,
      },
    }));
    const presentation: GroupRenderNode[] = activeCollapseGroups.map((group, index) => ({
      id: `group:${group.id}`,
      type: "collapseGroup",
      position: byId.get(`group:${group.id}`) ?? { x: 80, y: (semantic.length + index) * 108 + 60 },
      draggable: false,
      selectable: true,
      focusable: true,
      width: NODE_WIDTH,
      height: 52,
      zIndex: 2,
      data: {
        group,
        onExpand: (groupId) => setCollapsedGroups((current) => {
          const next = new Set(current);
          next.delete(groupId);
          return next;
        }),
      },
    }));
    const regions: RegionRenderNode[] = graph.regions.flatMap((region) => {
      const members = region.member_node_ids
        .map((id) => byId.get(id))
        .filter((value): value is { id: string; x: number; y: number } => Boolean(value));
      if (!members.length) return [];
      const minX = Math.min(...members.map((item) => item.x)) - 28;
      const minY = Math.min(...members.map((item) => item.y)) - 48;
      const maxX = Math.max(...members.map((item) => item.x)) + NODE_WIDTH + 28;
      const maxY = Math.max(...members.map((item) => item.y)) + NODE_HEIGHT + 28;
      return [{
        id: region.id,
        type: "regionNode" as const,
        position: { x: minX, y: minY },
        style: { width: maxX - minX, height: maxY - minY },
        draggable: false,
        selectable: true,
        focusable: true,
        zIndex: 0,
        data: {
          label: region.type === "unscoped" ? "Run 级操作" : region.label,
          status: region.status,
          terminalStatus: region.terminal_status ?? region.status,
          healthStatus: region.health_status ?? region.status,
          laneKind: region.lane_kind ?? null,
          count: members.length,
        },
      }];
    });
    return [...regions, ...semantic, ...presentation];
  }, [activeCollapseGroups, expandedGroups, graph.expansion_groups, graph.regions, highlighted, positions, selectedNodeId, toggleExpand, visibleSemantic]);

  const renderEdges = useMemo<Edge[]>(() => graph.edges
    .filter((edge) => !hiddenAttemptIds.has(edge.source) && !hiddenAttemptIds.has(edge.target) && !hiddenCollapsedIds.has(edge.source) && !hiddenCollapsedIds.has(edge.target))
    .map((edge) => ({
      ...edge,
      type: "audit",
      data: { auditType: edge.relation ?? edge.type },
      sourceHandle: edge.type === "spawn_branch"
        ? (graph.nodes.find((node) => node.id === edge.target)?.lane_side === "left" ? "left-source" : "right-source")
        : "bottom-source",
      targetHandle: edge.type === "result_return"
        ? (graph.nodes.find((node) => node.id === edge.source)?.lane_side === "left" ? "left-target" : "right-target")
        : "top-target",
      markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12 },
      zIndex: ["caused_by", "spawn_branch", "result_return"].includes(edge.type) ? 3 : 1,
      style: highlighted.size > 0 && (!highlighted.has(edge.source) || !highlighted.has(edge.target))
        ? { opacity: 0.16 }
        : undefined,
    })), [graph.edges, hiddenAttemptIds, hiddenCollapsedIds, highlighted]);

  const locateFirstAnomaly = () => {
    if (!graph.first_anomaly) return;
    onSelectNode(graph.first_anomaly.node_id);
    onFocusMode("causal");
    void flowRef.current?.fitView({ nodes: [{ id: graph.first_anomaly.node_id }], duration: 260, padding: 0.8 });
  };

  return (
    <div
      className="relative h-full w-full bg-background"
      data-testid="trace-graph"
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        const target = event.target;
        if (!(target instanceof HTMLElement) || target.closest("button")) return;
        const wrapper = target.closest<HTMLElement>(".react-flow__node[data-id]");
        const nodeId = wrapper?.dataset.id;
        if (!nodeId || !visibleSemantic.some((node) => node.id === nodeId)) return;
        event.preventDefault();
        onSelectNode(nodeId);
      }}
    >
      <ReactFlow<RenderNode, Edge>
        nodes={renderNodes}
        edges={renderEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        minZoom={0.035}
        maxZoom={1.6}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        nodesDraggable={false}
        onInit={(instance) => { flowRef.current = instance; }}
        onNodeClick={(_, node) => {
          if (node.type === "traceNode") onSelectNode(node.id);
        }}
        onNodeDoubleClick={(_, node) => {
          if (node.type !== "traceNode") return;
          const semantic = graph.nodes.find((candidate) => candidate.id === node.id);
          if (semantic?.expandable) toggleExpand(semantic);
        }}
        onPaneClick={() => onSelectNode(null)}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={22} size={1} color="hsl(var(--border) / .45)" />
        <Controls position="bottom-left" showInteractive={false} />
        {visibleSemantic.length > 24 ? <MiniMap position="bottom-right" pannable zoomable /> : null}
      </ReactFlow>
      <div className="absolute left-3 top-3 z-10 flex items-center gap-1 rounded-md border border-border/70 bg-background/95 p-1 shadow-sm backdrop-blur">
        <Button type="button" variant="ghost" size="icon" className="h-8 w-8" aria-label="适配整张图" title="适配整张图" onClick={() => void flowRef.current?.fitView({ padding: 0.2, duration: 200 })}>
          <Focus className="h-3.5 w-3.5" />
        </Button>
        <Button type="button" variant="ghost" size="icon" className="h-8 w-8" aria-label="定位主轴" title="定位主轴" onClick={() => {
          const main = visibleSemantic.find((node) => node.lane_kind === "main" && node.type !== "run")
            ?? visibleSemantic.find((node) => node.lane_kind === "main")
            ?? visibleSemantic[0];
          if (main) void flowRef.current?.fitView({ nodes: [{ id: main.id }], duration: 200, padding: 0.8 });
        }}>
          <LocateFixed className="h-3.5 w-3.5" />
        </Button>
        <Button type="button" variant="ghost" size="icon" className={cn("h-8 w-8", graph.first_anomaly && "text-amber-600")} disabled={!graph.first_anomaly} aria-label="定位首个异常" title="定位首个异常" onClick={locateFirstAnomaly}>
          <AlertTriangle className="h-3.5 w-3.5" />
        </Button>
        <Button type="button" variant="ghost" size="icon" className="h-8 w-8" aria-label="图例" title="顺序 / 分支 / 结果回流 / 因果 / 重试">
          <Network className="h-3.5 w-3.5" />
        </Button>
        {visibleSemantic.length > 100 ? <span className="px-1 text-[10px] text-muted-foreground"><MapIcon className="mr-1 inline h-3 w-3" />{visibleSemantic.length}</span> : null}
      </div>
    </div>
  );
}
