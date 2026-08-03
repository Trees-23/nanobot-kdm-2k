import ELK from "elkjs/lib/elk.bundled.js";
import type { ELK as ElkInstance } from "elkjs/lib/elk-api";

import {
  buildLocalRelationRoutes,
  type RelationRoute,
  type RouteBounds,
  type RouteNodeBounds,
} from "@/components/traces/toolRelationRouting";

type LayoutNode = {
  id: string;
  width: number;
  height: number;
  laneId: string;
  laneOrder: number;
  order: number;
  runKind: "main" | "child_agent" | "continuation" | "unknown";
};

type LayoutEdge = {
  id: string;
  source: string;
  target: string;
  relation: string;
};

type LayoutRequest = {
  id: number;
  nodes: LayoutNode[];
  edges: LayoutEdge[];
};

export type LayoutPosition = { id: string; x: number; y: number };

export type AuditLayoutResponse = {
  id: number;
  positions: LayoutPosition[];
  routes: RelationRoute[];
  layoutBounds: RouteBounds | null;
  warning?: string;
};

const CENTER_X = 960;
const LANE_PITCH = 336;
const ROW_GAP = 54;
const TOP = 84;

export const ELK_LAYOUT_OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.portConstraints": "FIXED_ORDER",
  "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
  "elk.spacing.nodeNode": String(ROW_GAP),
  "elk.spacing.edgeNode": "18",
  "elk.layered.spacing.nodeNodeBetweenLayers": String(ROW_GAP),
} as const;

function unionBounds(bounds: readonly RouteNodeBounds[]): RouteBounds | null {
  if (!bounds.length) return null;
  const minX = Math.min(...bounds.map((bound) => bound.x));
  const minY = Math.min(...bounds.map((bound) => bound.y));
  const maxX = Math.max(...bounds.map((bound) => bound.x + bound.width));
  const maxY = Math.max(...bounds.map((bound) => bound.y + bound.height));
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

function fallbackPositions(nodes: readonly LayoutNode[]): LayoutPosition[] {
  const lanes = new Map<string, LayoutNode[]>();
  for (const node of nodes) {
    const lane = lanes.get(node.laneId) ?? [];
    lane.push(node);
    lanes.set(node.laneId, lane);
  }
  return [...lanes.values()]
    .flatMap((lane) => lane.sort((a, b) => a.order - b.order || a.id.localeCompare(b.id))
      .map((node, index) => ({
        id: node.id,
        x: CENTER_X + node.laneOrder * LANE_PITCH,
        y: TOP + index * (node.height + ROW_GAP),
      })))
    .sort((a, b) => a.id.localeCompare(b.id));
}

async function layoutLane(elk: ElkInstance, lane: readonly LayoutNode[], edges: readonly LayoutEdge[]): Promise<Map<string, number>> {
  const ordered = [...lane].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
  if (ordered.length < 2) return new Map(ordered.map((node) => [node.id, TOP]));
  const ids = new Set(ordered.map((node) => node.id));
  const graph = await elk.layout({
    id: `lane:${ordered[0].laneId}`,
    layoutOptions: ELK_LAYOUT_OPTIONS,
    children: ordered.map((node) => ({ id: node.id, width: node.width, height: node.height })),
    edges: edges
      .filter((edge) => ids.has(edge.source) && ids.has(edge.target) && edge.relation === "sequence")
      .sort((a, b) => a.id.localeCompare(b.id))
      .map((edge) => ({ id: edge.id, sources: [edge.source], targets: [edge.target] })),
  });
  const elkY = new Map((graph.children ?? []).map((node) => [node.id, node.y ?? 0]));
  const rank = [...ordered].sort((a, b) => (elkY.get(a.id) ?? 0) - (elkY.get(b.id) ?? 0)
    || a.order - b.order || a.id.localeCompare(b.id));
  let y = TOP;
  return new Map(rank.map((node) => {
    const result: [string, number] = [node.id, y];
    y += node.height + ROW_GAP;
    return result;
  }));
}

async function layout(request: LayoutRequest): Promise<AuditLayoutResponse> {
  const byLane = new Map<string, LayoutNode[]>();
  for (const node of request.nodes) {
    const lane = byLane.get(node.laneId) ?? [];
    lane.push(node);
    byLane.set(node.laneId, lane);
  }
  const elk = new ELK();
  const lanePositions = await Promise.all([...byLane.entries()]
    .sort(([first], [second]) => first.localeCompare(second))
    .map(async ([, nodes]) => layoutLane(elk, nodes, request.edges)));
  elk.terminateWorker();
  const yById = new Map(lanePositions.flatMap((positions) => [...positions]));
  const positions = [...request.nodes]
    .sort((a, b) => a.id.localeCompare(b.id))
    .map((node) => ({
      id: node.id,
      x: CENTER_X + node.laneOrder * LANE_PITCH,
      y: yById.get(node.id) ?? TOP + node.order * (node.height + ROW_GAP),
    }));
  const positionById = new Map(positions.map((position) => [position.id, position]));
  const bounds: RouteNodeBounds[] = request.nodes.map((node) => {
    const position = positionById.get(node.id)!;
    return {
      id: node.id,
      x: position.x,
      y: position.y,
      width: node.width,
      height: node.height,
      laneSide: node.laneOrder < 0 ? "left" : node.laneOrder > 0 ? "right" : "center",
    };
  });
  // Region headers are routing obstacles too, but remain presentation-only nodes in React Flow.
  for (const nodes of byLane.values()) {
    const members = nodes.map((node) => bounds.find((bound) => bound.id === node.id)!).filter(Boolean);
    if (!members.length) continue;
    const first = members.reduce((candidate, member) => member.y < candidate.y ? member : candidate);
    bounds.push({
      id: `header:${nodes[0].laneId}`,
      x: first.x - 28,
      y: first.y - 48,
      width: first.width + 56,
      height: 36,
      laneSide: first.laneSide,
    });
  }
  const routes = [...buildLocalRelationRoutes({
    edges: request.edges.map((edge) => ({ ...edge, type: edge.relation })),
    nodeBounds: bounds,
  }).values()];
  return { id: request.id, positions, routes, layoutBounds: unionBounds(bounds.filter((bound) => !bound.id.startsWith("header:"))) };
}

self.onmessage = (event: MessageEvent<LayoutRequest>) => {
  const request = event.data;
  void layout(request)
    .then((response) => self.postMessage(response))
    .catch((error: unknown) => {
      const positions = fallbackPositions(request.nodes);
      const positionById = new Map(positions.map((position) => [position.id, position]));
      const bounds = request.nodes.map((node) => ({
        id: node.id,
        x: positionById.get(node.id)?.x ?? CENTER_X,
        y: positionById.get(node.id)?.y ?? TOP,
        width: node.width,
        height: node.height,
        laneSide: node.laneOrder < 0 ? "left" as const : node.laneOrder > 0 ? "right" as const : "center" as const,
      }));
      self.postMessage({
        id: request.id,
        positions,
        routes: [...buildLocalRelationRoutes({
          edges: request.edges.map((edge) => ({ ...edge, type: edge.relation })),
          nodeBounds: bounds,
        }).values()],
        layoutBounds: unionBounds(bounds),
        warning: error instanceof Error ? `elk_failed:${error.message}` : "elk_failed",
      } satisfies AuditLayoutResponse);
    });
};

export {};
