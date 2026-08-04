import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from "react";
import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { AuditGraphEdge, AuditGraphNode, AuditGraphResponse, TraceEdgeType } from "@/lib/audit-types";
import { auditNodeTypeLabel, auditStatusLabel } from "@/lib/audit-display";
import { cn } from "@/lib/utils";
import type { TraceFocusMode } from "@/components/traces/TraceNodeInspector";
import {
  buildLocalRelationRoutes,
  relationPorts,
  SECONDARY_RELATION_TYPES,
  STRUCTURAL_RELATION_TYPES,
  type RouteBounds,
  type RouteNodeBounds,
  type RelationRoute,
} from "@/components/traces/toolRelationRouting";
import type { AuditLayoutResponse } from "@/workers/auditLayout.worker";

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
  const relationRoute = props.data?.relationRoute as RelationRoute | undefined;
  const label = props.data?.label as string | undefined;
  const active = Boolean(props.data?.active);
  const [smoothPath] = getSmoothStepPath(props);
  const path = relationRoute?.path ?? smoothPath;
  const styles: Record<TraceEdgeType, { stroke: string; dash?: string; width: number }> = {
    sequence: { stroke: "hsl(var(--muted-foreground) / .35)", width: 1 },
    spawn_branch: { stroke: "#0f766e", width: 2 },
    result_return: { stroke: "#2563eb", dash: "7 4", width: 1.8 },
    caused_by: { stroke: "hsl(var(--foreground) / .8)", width: 1.8 },
    retry: { stroke: "#d97706", dash: "3 3", width: 1.5 },
    parent_run: { stroke: "hsl(var(--foreground) / .55)", width: 1.4 },
    resumed_from: { stroke: "#3b82f6", dash: "6 4", width: 1.5 },
    retry_of: { stroke: "#d97706", dash: "3 3", width: 1.5 },
    tool_retry: { stroke: "#ca8a04", dash: "3 3", width: 1.7 },
    tool_continuation: { stroke: "#2563eb", dash: "7 4", width: 1.7 },
    tool_recovery: { stroke: "#0891b2", dash: "2 5", width: 2 },
    task_execution: { stroke: "#7c3aed", width: 2 },
    task_replacement: { stroke: "#d97706", dash: "5 3", width: 1.8 },
    task_recovery: { stroke: "#0891b2", dash: "2 5", width: 2 },
  };
  const style = styles[type ?? "sequence"];
  return (
    <>
      <BaseEdge
        path={path}
        markerStart={props.markerStart}
        markerEnd={props.markerEnd}
        interactionWidth={Math.max(props.interactionWidth ?? 20, 32)}
        style={{
          ...props.style,
          stroke: style.stroke,
          strokeWidth: active ? style.width + 0.8 : style.width,
          strokeDasharray: style.dash,
        }}
      />
      {active && label && relationRoute?.points.length ? (
        <EdgeLabelRenderer>
          <span
            className="pointer-events-none absolute rounded border border-border/80 bg-background px-1.5 py-0.5 text-[10px] font-medium text-foreground shadow-sm"
            style={{
              transform: `translate(-50%, -50%) translate(${relationRoute.points[Math.floor(relationRoute.points.length / 2)].x}px, ${relationRoute.points[Math.floor(relationRoute.points.length / 2)].y}px)`,
            }}
          >
            {label}
          </span>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

const edgeTypes = { audit: AuditEdge };
const toolRelationLabels: Partial<Record<TraceEdgeType, string>> = {
  spawn_branch: "子任务分支",
  result_return: "结果回传",
  resumed_from: "Run 恢复关系",
  tool_retry: "Tool 重试关系",
  tool_continuation: "Tool 继续关系",
  tool_recovery: "Tool 恢复关系",
  task_execution: "Task 执行关系",
  task_replacement: "Task 替换关系",
  task_recovery: "Task 恢复关系",
};
const relationShortLabels: Partial<Record<TraceEdgeType, string>> = {
  spawn_branch: "分支",
  task_execution: "执行",
  result_return: "结果",
  task_replacement: "替换",
  task_recovery: "恢复",
  resumed_from: "恢复",
  tool_retry: "重试",
  tool_continuation: "继续",
  tool_recovery: "恢复",
};

export function edgeHandles(edge: AuditGraphEdge, graph: AuditGraphResponse) {
  const sourceNode = graph.nodes.find((node) => node.id === edge.source);
  const targetNode = graph.nodes.find((node) => node.id === edge.target);
  if (!sourceNode || !targetNode) return { sourceHandle: "bottom-sequence-source", targetHandle: "top-sequence-target" };
  const source = { id: edge.source, x: sourceNode.lane_order ?? 0, y: 0, width: 1, height: 1, laneSide: sourceNode.lane_side };
  const target = { id: edge.target, x: targetNode.lane_order ?? 0, y: 0, width: 1, height: 1, laneSide: targetNode.lane_side };
  const ports = relationPorts(edge, source, target);
  return { sourceHandle: ports.sourcePort.id, targetHandle: ports.targetPort.id };
}

function unionBounds(bounds: readonly RouteBounds[]): RouteBounds | null {
  if (!bounds.length) return null;
  const minX = Math.min(...bounds.map((item) => item.x));
  const minY = Math.min(...bounds.map((item) => item.y));
  const maxX = Math.max(...bounds.map((item) => item.x + item.width));
  const maxY = Math.max(...bounds.map((item) => item.y + item.height));
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

interface FocusResult {
  nodeIds: Set<string>;
  edgeIds: Set<string>;
}

function relatedIds(
  graph: AuditGraphResponse,
  selectedId: string | null,
  mode: TraceFocusMode,
): FocusResult {
  if (!selectedId || !mode) return { nodeIds: new Set(), edgeIds: new Set() };
  const allowed: Record<Exclude<TraceFocusMode, null>, TraceEdgeType[]> = {
    causal: ["caused_by", "retry", "retry_of"],
    context: ["sequence"],
    branch: ["spawn_branch", "parent_run", "task_execution", "task_replacement"],
    result: ["result_return"],
    recovery: ["resumed_from", "tool_recovery", "task_recovery"],
  };
  const result = new Set([selectedId]);
  const edgeIds = new Set<string>();
  let changed = true;
  while (changed) {
    changed = false;
    graph.edges.forEach((edge) => {
      if (!allowed[mode].includes(edge.type)) return;
      if (result.has(edge.source) || result.has(edge.target)) {
        if (!result.has(edge.source) || !result.has(edge.target)) changed = true;
        result.add(edge.source);
        result.add(edge.target);
        edgeIds.add(edge.id);
      }
    });
  }
  return edgeIds.size
    ? { nodeIds: result, edgeIds }
    : { nodeIds: new Set(), edgeIds: new Set() };
}

function motionDuration(duration: number): number {
  return typeof window !== "undefined"
    && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    ? 0
    : duration;
}

function fallbackPositions(nodes: AuditGraphNode[]) {
  return nodes.map((node, index) => ({
    id: node.id,
    x: 960 + (node.lane_order ?? 0) * 408,
    y: index * 136 + 84,
  }));
}

export function TraceGraph({
  graph,
  selectedNodeId,
  focusMode,
  onSelectNode,
  onFocusMode,
  onSelectEdge,
}: {
  graph: AuditGraphResponse;
  selectedNodeId: string | null;
  focusMode: TraceFocusMode;
  onSelectNode: (nodeId: string | null) => void;
  onFocusMode: (mode: TraceFocusMode) => void;
  onSelectEdge?: (edge: AuditGraphEdge) => void;
}) {
  const [layoutResult, setLayoutResult] = useState<Pick<AuditLayoutResponse, "positions" | "routes" | "layoutBounds" | "warning">>({
    positions: [],
    routes: [],
    layoutBounds: null,
  });
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    () => new Set(graph.expansion_groups.filter((group) => group.default_expanded).map((group) => group.id)),
  );
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(
    () => new Set(graph.collapse_groups.map((group) => group.id)),
  );
  const flowRef = useRef<ReactFlowInstance<RenderNode, Edge> | null>(null);
  const requestId = useRef(0);
  const deliveredRelationId = useRef<string | null>(null);
  const legendRef = useRef<HTMLDivElement>(null);
  const [legendOpen, setLegendOpen] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [activeRelationId, setActiveRelationId] = useState<string | null>(null);

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
      const positions = [
        ...fallback,
        ...activeCollapseGroups.map((group, index) => ({ id: `group:${group.id}`, x: 80, y: (fallback.length + index) * 132 + 60 })),
      ];
      setLayoutResult({ positions, routes: [], layoutBounds: null, warning: "worker_unavailable" });
      return;
    }
    const worker = new Worker(new URL("../../workers/auditLayout.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (event: MessageEvent<AuditLayoutResponse>) => {
      if (event.data.id === id && event.data.positions) {
        setLayoutResult(event.data);
      }
      worker.terminate();
    };
    worker.onerror = () => {
      setLayoutResult({ positions: fallbackPositions(visibleSemantic), routes: [], layoutBounds: null, warning: "worker_error" });
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

  const focusResult = useMemo(
    () => relatedIds(graph, selectedNodeId, focusMode),
    [focusMode, graph, selectedNodeId],
  );
  const highlighted = focusResult.nodeIds;

  useEffect(() => {
    if (!activeRelationId) {
      deliveredRelationId.current = null;
      return;
    }
    if (deliveredRelationId.current === activeRelationId) return;
    const edge = graph.edges.find((candidate) => candidate.id === activeRelationId);
    if (!edge) return;
    deliveredRelationId.current = activeRelationId;
    onSelectEdge?.(edge);
  }, [activeRelationId, graph.edges, onSelectEdge]);

  useEffect(() => {
    if (!focusMode || !focusResult.nodeIds.size) return;
    void flowRef.current?.fitView({
      nodes: [...focusResult.nodeIds].map((id) => ({ id })),
      duration: motionDuration(220),
      padding: 0.35,
    });
  }, [focusMode, focusResult]);

  useEffect(() => {
    if (!legendOpen) return;
    legendRef.current?.focus();
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setLegendOpen(false);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [legendOpen]);

  useEffect(() => {
    if (!feedback) return;
    const timer = window.setTimeout(() => setFeedback(null), 2_000);
    return () => window.clearTimeout(timer);
  }, [feedback]);

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

  const geometryNodes = useMemo<RenderNode[]>(() => {
    const byId = new Map(layoutResult.positions.map((position) => [position.id, position]));
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
        selected: false,
        dimmed: false,
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
  }, [activeCollapseGroups, expandedGroups, graph.expansion_groups, graph.regions, layoutResult.positions, toggleExpand, visibleSemantic]);

  const renderNodes = useMemo<RenderNode[]>(() => {
    const activeRelation = activeRelationId ? graph.edges.find((edge) => edge.id === activeRelationId) : null;
    const activeEndpoints = new Set(activeRelation ? [activeRelation.source, activeRelation.target] : []);
    return geometryNodes.map((node) => {
    if (node.type !== "traceNode") return node;
    return {
      ...node,
      data: {
        ...node.data,
        selected: selectedNodeId === node.id,
        dimmed: (highlighted.size > 0 && !highlighted.has(node.id))
          || (activeEndpoints.size > 0 && !activeEndpoints.has(node.id)),
      },
    };
    });
  }, [activeRelationId, geometryNodes, graph.edges, highlighted, selectedNodeId]);

  const availableGraphEdges = useMemo(() => graph.edges
    .filter((edge) => !hiddenAttemptIds.has(edge.source) && !hiddenAttemptIds.has(edge.target) && !hiddenCollapsedIds.has(edge.source) && !hiddenCollapsedIds.has(edge.target))
    .filter((edge) => geometryNodes.some((node) => node.id === edge.source) && geometryNodes.some((node) => node.id === edge.target)),
  [geometryNodes, graph.edges, hiddenAttemptIds, hiddenCollapsedIds]);

  const defaultResultReturnIds = useMemo(() => {
    const resultReturns = availableGraphEdges.filter((edge) => edge.type === "result_return");
    return resultReturns.length > 1 ? new Set(resultReturns.map((edge) => edge.id)) : new Set<string>();
  }, [availableGraphEdges]);

  const visibleGraphEdges = useMemo(() => availableGraphEdges.filter((edge) => STRUCTURAL_RELATION_TYPES.has(edge.type)
    || defaultResultReturnIds.has(edge.id)
    || focusResult.edgeIds.has(edge.id)
    || activeRelationId === edge.id
    || (Boolean(selectedNodeId) && SECONDARY_RELATION_TYPES.has(edge.type)
      && (edge.source === selectedNodeId || edge.target === selectedNodeId))),
  [activeRelationId, availableGraphEdges, defaultResultReturnIds, focusResult.edgeIds, selectedNodeId]);

  const visibleNodeBounds = useMemo<RouteNodeBounds[]>(() => geometryNodes.flatMap((node) => {
    const styleWidth = typeof node.style?.width === "number" ? node.style.width : null;
    const styleHeight = typeof node.style?.height === "number" ? node.style.height : null;
    const width = node.width ?? styleWidth;
    const height = node.height ?? styleHeight;
    if (width == null || height == null) return [];
    const semantic = graph.nodes.find((candidate) => candidate.id === node.id);
    const group = node.type === "collapseGroup"
      ? graph.collapse_groups.find((candidate) => `group:${candidate.id}` === node.id)
      : null;
    const groupMember = group
      ? graph.nodes.find((candidate) => group.member_node_ids.includes(candidate.id))
      : null;
    return [{
      id: node.id,
      x: node.position.x,
      y: node.position.y,
      width,
      height,
      laneSide: semantic?.lane_side ?? groupMember?.lane_side,
    }];
  }), [geometryNodes, graph.collapse_groups, graph.nodes]);

  const routeNodeBounds = useMemo(() => visibleNodeBounds.filter((bounds) => geometryNodes.find((node) => node.id === bounds.id)?.type !== "regionNode"), [geometryNodes, visibleNodeBounds]);

  const relationRoutes = useMemo(() => {
    const fromWorker = new Map(layoutResult.routes.map((route) => [route.edgeId, route]));
    const localFallback = buildLocalRelationRoutes({ edges: visibleGraphEdges, nodeBounds: routeNodeBounds });
    return new Map(visibleGraphEdges.map((edge) => [edge.id, fromWorker.get(edge.id) ?? localFallback.get(edge.id)]).filter((entry): entry is [string, RelationRoute] => Boolean(entry[1])));
  }, [layoutResult.routes, routeNodeBounds, visibleGraphEdges]);

  const activeEdgeIds = useMemo(() => new Set([
    ...focusResult.edgeIds,
    ...(activeRelationId ? [activeRelationId] : []),
    ...availableGraphEdges.filter((edge) => Boolean(selectedNodeId) && SECONDARY_RELATION_TYPES.has(edge.type)
      && (edge.source === selectedNodeId || edge.target === selectedNodeId)).map((edge) => edge.id),
  ]), [activeRelationId, availableGraphEdges, focusResult.edgeIds, selectedNodeId]);

  const renderEdges = useMemo<Edge[]>(() => visibleGraphEdges
    .map((edge) => ({
      ...edge,
      ...edgeHandles(edge, graph),
      type: "audit",
      data: {
        auditType: edge.relation ?? edge.type,
        relationRoute: relationRoutes.get(edge.id),
        active: activeEdgeIds.has(edge.id),
        label: relationShortLabels[edge.type],
      },
      markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12 },
      zIndex: 0,
      style: (highlighted.size > 0 || activeEdgeIds.size > 0) && !activeEdgeIds.has(edge.id)
        ? { opacity: 0.16 }
        : undefined,
    })), [activeEdgeIds, graph, highlighted, relationRoutes, visibleGraphEdges]);

  const executionBounds = useMemo(() => unionBounds(visibleNodeBounds), [visibleNodeBounds]);
  const executionBoundsKey = executionBounds
    ? `${executionBounds.x}:${executionBounds.y}:${executionBounds.width}:${executionBounds.height}`
    : "";

  useEffect(() => {
    if (!executionBounds || !layoutResult.positions.length) return;
    const frame = window.requestAnimationFrame(() => {
      void flowRef.current?.fitBounds(executionBounds, { padding: 0.24, duration: 0 });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [executionBoundsKey, layoutResult.positions.length]);

  const relationEdges = useMemo(
    () => graph.edges.filter((edge) => SECONDARY_RELATION_TYPES.has(edge.type)),
    [graph.edges],
  );

  const selectEdge = useCallback((edge: AuditGraphEdge) => {
    setActiveRelationId(edge.id);
    const route = relationRoutes.get(edge.id);
    const endpointBounds = routeNodeBounds.filter((bounds) => bounds.id === edge.source || bounds.id === edge.target);
    const bounds = unionBounds(route ? [...endpointBounds, route.bounds] : endpointBounds);
    if (bounds) {
      void flowRef.current?.fitBounds(bounds, {
        duration: motionDuration(200),
        padding: 0.35,
      });
      return;
    }
    void flowRef.current?.fitView({
      nodes: [{ id: edge.source }, { id: edge.target }],
      duration: motionDuration(200),
      padding: 0.8,
    });
  }, [relationRoutes, routeNodeBounds]);

  const locateFirstAnomaly = () => {
    if (!graph.first_anomaly) return;
    onSelectNode(graph.first_anomaly.node_id);
    onFocusMode("causal");
    void flowRef.current?.fitView({ nodes: [{ id: graph.first_anomaly.node_id }], duration: motionDuration(260), padding: 0.8 });
    const anomaly = graph.nodes.find((node) => node.id === graph.first_anomaly?.node_id);
    setFeedback(anomaly?.summary.error_summary ?? "已定位首个异常");
  };

  const focusLabels: Record<Exclude<TraceFocusMode, null>, string> = {
    causal: "因果链",
    context: "执行上下文",
    branch: "结构分支",
    result: "结果回传",
    recovery: "恢复关系",
  };

  const toolbarButton = (
    label: string,
    button: ReactElement,
  ) => (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );

  return (
    <div
      className="relative h-full w-full bg-background"
      data-testid="trace-graph"
      data-geometry-key={JSON.stringify(geometryNodes.map((node) => [node.id, node.position.x, node.position.y]))}
      data-relation-routes={JSON.stringify([...relationRoutes.values()].map((route) => ({
        edgeId: route.edgeId,
        points: route.points,
        bends: route.bendCount,
        routeLength: route.routeLength,
        detourRatio: route.detourRatio,
        fallbackReason: route.fallbackReason ?? null,
      })))}
      data-layout-warning={layoutResult.warning ?? undefined}
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
        nodesDraggable={false}
        autoPanOnNodeFocus={false}
        onInit={(instance) => { flowRef.current = instance; }}
        onNodeClick={(_, node) => {
          if (node.type === "traceNode") onSelectNode(node.id);
        }}
        onNodeDoubleClick={(_, node) => {
          if (node.type !== "traceNode") return;
          const semantic = graph.nodes.find((candidate) => candidate.id === node.id);
          if (semantic?.expandable) toggleExpand(semantic);
        }}
        onEdgeClick={(_, edge) => {
          const selected = graph.edges.find((candidate) => candidate.id === edge.id);
          if (selected) selectEdge(selected);
        }}
        onPaneClick={() => {
          setActiveRelationId(null);
          onSelectNode(null);
        }}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={22} size={1} color="hsl(var(--border) / .45)" />
        <Controls position="bottom-left" showInteractive={false} />
        {visibleSemantic.length > 24 ? <MiniMap position="bottom-right" pannable zoomable /> : null}
      </ReactFlow>
      <TooltipProvider delayDuration={180} skipDelayDuration={60}>
        <div className="absolute left-3 top-3 z-10 flex items-center gap-1 rounded-md border border-border/70 bg-background/95 p-1 shadow-sm backdrop-blur">
          {toolbarButton("适配执行结构", (
            <Button type="button" variant="ghost" size="icon" className="h-8 w-8" aria-label="适配执行结构" onClick={() => {
              if (executionBounds) {
                void flowRef.current?.fitBounds(executionBounds, { padding: 0.24, duration: motionDuration(200) });
              } else {
                void flowRef.current?.fitView({ padding: 0.2, duration: motionDuration(200) });
              }
              setFeedback("已适配执行结构");
            }}>
              <Focus className="h-3.5 w-3.5" />
            </Button>
          ))}
          {toolbarButton("定位 main Run", (
            <Button type="button" variant="ghost" size="icon" className="h-8 w-8" aria-label="定位 main Run" onClick={() => {
              const main = visibleSemantic.find((node) => node.type === "run" && node.run_kind === "main")
                ?? visibleSemantic.find((node) => node.lane_kind === "main")
                ?? visibleSemantic[0];
              if (!main) return;
              onSelectNode(main.id);
              void flowRef.current?.fitView({ nodes: [{ id: main.id }], duration: motionDuration(200), padding: 0.8 });
              setFeedback("已定位 main Run");
            }}>
              <LocateFixed className="h-3.5 w-3.5" />
            </Button>
          ))}
          {toolbarButton("定位首个异常", (
            <Button type="button" variant="ghost" size="icon" className={cn("h-8 w-8", graph.first_anomaly && "text-amber-600")} disabled={!graph.first_anomaly} aria-label="定位首个异常" onClick={locateFirstAnomaly}>
              <AlertTriangle className="h-3.5 w-3.5" />
            </Button>
          ))}
          {toolbarButton("打开图例", (
            <Button type="button" variant="ghost" size="icon" className="h-8 w-8" aria-label="图例" aria-expanded={legendOpen} onClick={() => setLegendOpen((open) => !open)}>
              <Network className="h-3.5 w-3.5" />
            </Button>
          ))}
          {visibleSemantic.length > 100 ? <span className="px-1 text-[10px] text-muted-foreground"><MapIcon className="mr-1 inline h-3 w-3" />{visibleSemantic.length}</span> : null}
        </div>
        {relationEdges.length ? (
          <div className={cn(
            "absolute right-3 z-10 flex max-w-[calc(100%-96px)] gap-1 overflow-x-auto rounded-md border border-border/70 bg-background/95 p-1 shadow-sm backdrop-blur",
            activeRelationId ? "bottom-3" : "top-3",
          )} data-testid="tool-relation-selector">
            {relationEdges.map((edge) => (
              <Button
                key={edge.id}
                type="button"
                variant="ghost"
                size="sm"
                className="h-8 shrink-0 px-2 text-[10px]"
                aria-label={toolRelationLabels[edge.type] ?? edge.type}
                title={toolRelationLabels[edge.type] ?? edge.type}
                data-testid={`tool-relation-${edge.type}`}
                onClick={() => selectEdge(edge)}
              >
                {relationShortLabels[edge.type] ?? "关系"} {graph.edges.filter((candidate) => candidate.type === edge.type).length}
              </Button>
            ))}
          </div>
        ) : null}
      </TooltipProvider>
      {focusMode ? (
        <div role="status" className="absolute left-3 top-14 z-10 flex items-center gap-2 rounded-md border border-border/70 bg-background/95 px-2.5 py-1.5 text-[10.5px] shadow-sm">
          <span>
            {focusLabels[focusMode]}：{focusResult.edgeIds.size
              ? `${focusResult.nodeIds.size} 个节点 / ${focusResult.edgeIds.size} 条边`
              : ["result", "recovery"].includes(focusMode) ? "0 个节点 / 0 条边" : "零命中"}
          </span>
          <Button type="button" variant="ghost" size="sm" className="h-6 px-1.5 text-[10px]" onClick={() => onFocusMode(null)}>清除</Button>
        </div>
      ) : null}
      {feedback ? (
        <p role="status" className="absolute left-3 top-24 z-10 max-w-sm rounded-md border border-border/70 bg-background/95 px-2.5 py-1.5 text-[10.5px] shadow-sm">
          {feedback}
        </p>
      ) : null}
      {legendOpen ? (
        <div ref={legendRef} role="dialog" aria-label="运行轨迹图例" tabIndex={-1} className="absolute left-3 top-14 z-20 w-80 rounded-md border border-border/70 bg-background p-3 text-[10.5px] shadow-lg outline-none">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold">图例</h3>
            <Button type="button" variant="ghost" size="sm" className="h-6 px-2 text-[10px]" onClick={() => setLegendOpen(false)}>关闭</Button>
          </div>
          <p className="mt-2 text-muted-foreground">绿色为成功，红色为失败，琥珀色表示过程警告；节点终态不会因恢复而改写。</p>
          <div className="mt-2 grid gap-1.5">
            <p><span className="mr-2 inline-block w-8 border-t border-muted-foreground" />顺序/父子：实线</p>
            <p><span className="mr-2 inline-block w-8 border-t-2 border-teal-700" />结构分支：绿色实线</p>
            <p><span className="mr-2 inline-block w-8 border-t-2 border-dashed border-blue-600" />结果回流：蓝色虚线</p>
            <p><span className="mr-2 inline-block w-8 border-t-2 border-dashed border-cyan-600" />Tool 恢复：青色点划线</p>
            <p><span className="mr-2 inline-block w-8 border-t-2 border-dashed border-amber-600" />重试：琥珀色虚线</p>
          </div>
          <p className="mt-2 text-muted-foreground">箭头从原因、父级或先前尝试指向结果、子级或后续尝试。</p>
        </div>
      ) : null}
    </div>
  );
}
