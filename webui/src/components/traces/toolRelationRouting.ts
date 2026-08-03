export const RELATION_RAIL_GAP = 56;
export const RELATION_SLOT_GAP = 14;
export const RELATION_CLEARANCE = 10;
export const RELATION_INTERVAL_GAP = 10;

export const ROUTED_RELATION_TYPES = new Set([
  "spawn_branch",
  "task_execution",
  "result_return",
  "task_replacement",
  "task_recovery",
  "resumed_from",
  "tool_retry",
  "tool_continuation",
  "tool_recovery",
]);

export interface RoutePoint {
  x: number;
  y: number;
}

export interface RouteNodeBounds {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  laneSide?: "left" | "center" | "right" | null;
}

export interface RelationEdgeInput {
  id: string;
  type: string;
  source: string;
  target: string;
}

export interface RouteBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface RelationRoute {
  edgeId: string;
  side: "left" | "right";
  slot: number;
  railX: number;
  points: readonly RoutePoint[];
  path: string;
  bounds: RouteBounds;
}

export interface RelationRouteInput {
  edges: readonly RelationEdgeInput[];
  nodeBounds: readonly RouteNodeBounds[];
  leftBoundary?: number;
  rightBoundary?: number;
  railGap?: number;
  slotGap?: number;
  clearance?: number;
  intervalGap?: number;
}

interface Segment {
  start: RoutePoint;
  end: RoutePoint;
}

interface RoutedEdge extends RelationEdgeInput {
  side: "left" | "right";
  sourceBounds: RouteNodeBounds;
  targetBounds: RouteNodeBounds;
  sourcePoint: RoutePoint;
  targetPoint: RoutePoint;
  startY: number;
  endY: number;
}

function right(bounds: RouteNodeBounds): number {
  return bounds.x + bounds.width;
}

function bottom(bounds: RouteNodeBounds): number {
  return bounds.y + bounds.height;
}

function overlapsInterval(
  first: readonly [number, number],
  second: readonly [number, number],
  gap: number,
): boolean {
  return first[0] < second[1] + gap && second[0] < first[1] + gap;
}

export function segmentIntersectsBounds(
  segment: Segment,
  bounds: RouteNodeBounds,
  clearance = 0,
): boolean {
  const left = bounds.x - clearance;
  const top = bounds.y - clearance;
  const boundsRight = right(bounds) + clearance;
  const boundsBottom = bottom(bounds) + clearance;
  if (segment.start.y === segment.end.y) {
    const minX = Math.min(segment.start.x, segment.end.x);
    const maxX = Math.max(segment.start.x, segment.end.x);
    return segment.start.y >= top
      && segment.start.y <= boundsBottom
      && maxX >= left
      && minX <= boundsRight;
  }
  if (segment.start.x === segment.end.x) {
    const minY = Math.min(segment.start.y, segment.end.y);
    const maxY = Math.max(segment.start.y, segment.end.y);
    return segment.start.x >= left
      && segment.start.x <= boundsRight
      && maxY >= top
      && minY <= boundsBottom;
  }
  return false;
}

function appendPoint(points: RoutePoint[], point: RoutePoint): void {
  const previous = points.at(-1);
  if (!previous || previous.x !== point.x || previous.y !== point.y) points.push(point);
}

export function buildOrthogonalRoutePath(points: readonly RoutePoint[]): string {
  if (!points.length) return "";
  return points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`)
    .join(" ");
}

function intersectsAny(
  start: RoutePoint,
  end: RoutePoint,
  obstacles: readonly RouteNodeBounds[],
  clearance: number,
): boolean {
  return obstacles.some((bounds) => segmentIntersectsBounds({ start, end }, bounds, clearance));
}

function corridorY(
  endpointY: number,
  obstacles: readonly RouteNodeBounds[],
  clearance: number,
): number {
  const above = Math.min(...obstacles.map((bounds) => bounds.y)) - clearance - 1;
  const below = Math.max(...obstacles.map((bounds) => bottom(bounds))) + clearance + 1;
  return endpointY - above <= below - endpointY ? above : below;
}

function endpoint(bounds: RouteNodeBounds, side: "left" | "right"): RoutePoint {
  return {
    x: side === "left" ? bounds.x : right(bounds),
    y: bounds.y + bounds.height / 2,
  };
}

function routePoints(
  edge: RoutedEdge,
  railX: number,
  nodeBounds: readonly RouteNodeBounds[],
  clearance: number,
): RoutePoint[] {
  const obstacles = nodeBounds.filter(
    (bounds) => bounds.id !== edge.source && bounds.id !== edge.target,
  );
  const escapeX = (bounds: RouteNodeBounds) => edge.side === "left"
    ? bounds.x - clearance - 1
    : right(bounds) + clearance + 1;
  const points: RoutePoint[] = [edge.sourcePoint];
  if (intersectsAny(edge.sourcePoint, { x: railX, y: edge.sourcePoint.y }, obstacles, clearance)) {
    const sourceEscape = { x: escapeX(edge.sourceBounds), y: edge.sourcePoint.y };
    const sourceCorridorY = corridorY(edge.sourcePoint.y, obstacles, clearance);
    const verticalAtEndpoint = { x: edge.sourcePoint.x, y: sourceCorridorY };
    if (!intersectsAny(edge.sourcePoint, verticalAtEndpoint, obstacles, clearance)) {
      appendPoint(points, verticalAtEndpoint);
    } else {
      appendPoint(points, sourceEscape);
      appendPoint(points, { x: sourceEscape.x, y: sourceCorridorY });
    }
    appendPoint(points, { x: railX, y: sourceCorridorY });
  } else {
    appendPoint(points, { x: railX, y: edge.sourcePoint.y });
  }

  if (intersectsAny({ x: railX, y: edge.targetPoint.y }, edge.targetPoint, obstacles, clearance)) {
    const targetEscape = { x: escapeX(edge.targetBounds), y: edge.targetPoint.y };
    const targetCorridorY = corridorY(edge.targetPoint.y, obstacles, clearance);
    const verticalAtEndpoint = { x: edge.targetPoint.x, y: targetCorridorY };
    appendPoint(points, { x: railX, y: targetCorridorY });
    if (!intersectsAny(verticalAtEndpoint, edge.targetPoint, obstacles, clearance)) {
      appendPoint(points, verticalAtEndpoint);
    } else {
      appendPoint(points, { x: targetEscape.x, y: targetCorridorY });
      appendPoint(points, targetEscape);
    }
  } else {
    appendPoint(points, { x: railX, y: edge.targetPoint.y });
  }
  appendPoint(points, edge.targetPoint);
  return points;
}

function routeBounds(points: readonly RoutePoint[]): RouteBounds {
  const minX = Math.min(...points.map((point) => point.x));
  const minY = Math.min(...points.map((point) => point.y));
  const maxX = Math.max(...points.map((point) => point.x));
  const maxY = Math.max(...points.map((point) => point.y));
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

function routeSide(source: RouteNodeBounds, target: RouteNodeBounds): "left" | "right" {
  const laneSide = target.laneSide === "center" ? source.laneSide : target.laneSide;
  return laneSide === "left" ? "left" : "right";
}

export function buildRelationRoutes(input: RelationRouteInput): Map<string, RelationRoute> {
  const byId = new Map(input.nodeBounds.map((bounds) => [bounds.id, bounds]));
  const routedEdges = input.edges.flatMap<RoutedEdge>((edge) => {
    if (!ROUTED_RELATION_TYPES.has(edge.type)) return [];
    const sourceBounds = byId.get(edge.source);
    const targetBounds = byId.get(edge.target);
    if (!sourceBounds || !targetBounds) return [];
    const side = routeSide(sourceBounds, targetBounds);
    const sourcePoint = endpoint(sourceBounds, side);
    const targetPoint = endpoint(targetBounds, side);
    return [{
      ...edge,
      side,
      sourceBounds,
      targetBounds,
      sourcePoint,
      targetPoint,
      startY: Math.min(sourcePoint.y, targetPoint.y),
      endY: Math.max(sourcePoint.y, targetPoint.y),
    }];
  }).sort((a, b) => a.side.localeCompare(b.side)
    || a.startY - b.startY
    || a.endY - b.endY
    || a.type.localeCompare(b.type)
    || a.id.localeCompare(b.id));

  const intervalGap = input.intervalGap ?? RELATION_INTERVAL_GAP;
  const slots = new Map<"left" | "right", Array<Array<readonly [number, number]>>>([
    ["left", []],
    ["right", []],
  ]);
  const assignments = new Map<string, number>();
  for (const edge of routedEdges) {
    const sideSlots = slots.get(edge.side)!;
    const interval = [edge.startY, edge.endY] as const;
    let slot = sideSlots.findIndex((intervals) => intervals.every(
      (occupied) => !overlapsInterval(interval, occupied, intervalGap),
    ));
    if (slot === -1) {
      slot = sideSlots.length;
      sideSlots.push([]);
    }
    sideSlots[slot].push(interval);
    assignments.set(edge.id, slot);
  }

  const railGap = input.railGap ?? RELATION_RAIL_GAP;
  const slotGap = input.slotGap ?? RELATION_SLOT_GAP;
  const clearance = input.clearance ?? RELATION_CLEARANCE;
  const visibleLeft = Math.min(input.leftBoundary ?? Number.POSITIVE_INFINITY, ...input.nodeBounds.map((bounds) => bounds.x));
  const visibleRight = Math.max(input.rightBoundary ?? Number.NEGATIVE_INFINITY, ...input.nodeBounds.map(right));
  const routes = new Map<string, RelationRoute>();
  for (const edge of routedEdges) {
    const slot = assignments.get(edge.id)!;
    const railX = edge.side === "left"
      ? visibleLeft - railGap - slot * slotGap
      : visibleRight + railGap + slot * slotGap;
    const points = routePoints(edge, railX, input.nodeBounds, clearance);
    routes.set(edge.id, {
      edgeId: edge.id,
      side: edge.side,
      slot,
      railX,
      points,
      path: buildOrthogonalRoutePath(points),
      bounds: routeBounds(points),
    });
  }
  return routes;
}

// Compatibility aliases for extensions importing the earlier Tool-specific names.
export const TOOL_RELATION_RAIL_GAP = RELATION_RAIL_GAP;
export type ToolRelationEdgeInput = RelationEdgeInput;
export type ToolRelationRoute = RelationRoute;
export type ToolRelationRouteInput = RelationRouteInput;
export const buildToolRelationRoutes = buildRelationRoutes;
