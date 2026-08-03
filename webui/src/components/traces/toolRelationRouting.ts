export const RELATION_CLEARANCE = 12;
export const STRUCTURAL_RELATION_TYPES = new Set([
  "sequence",
  "spawn_branch",
  "task_execution",
]);

export const SECONDARY_RELATION_TYPES = new Set([
  "result_return",
  "retry",
  "retry_of",
  "tool_retry",
  "resumed_from",
  "tool_recovery",
  "task_recovery",
  "task_replacement",
  "tool_continuation",
  "parent_run",
  "caused_by",
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

export type RelationPortKind = "sequence" | "structure" | "result" | "recovery";
export type RelationPortSide = "top" | "bottom" | "left" | "right";

export interface RelationPort {
  id: string;
  side: RelationPortSide;
  point: RoutePoint;
}

export interface RelationRoute {
  edgeId: string;
  points: readonly RoutePoint[];
  path: string;
  bounds: RouteBounds;
  sourcePort: RelationPort;
  targetPort: RelationPort;
  bendCount: number;
  routeLength: number;
  manhattanDistance: number;
  detourRatio: number;
  fallbackReason?: string;
}

export interface RelationRouteInput {
  edges: readonly RelationEdgeInput[];
  nodeBounds: readonly RouteNodeBounds[];
  clearance?: number;
}

function right(bounds: RouteNodeBounds): number {
  return bounds.x + bounds.width;
}

function bottom(bounds: RouteNodeBounds): number {
  return bounds.y + bounds.height;
}

function pointOnSide(
  bounds: RouteNodeBounds,
  side: RelationPortSide,
  offset: number,
): RoutePoint {
  if (side === "left") return { x: bounds.x, y: bounds.y + bounds.height * offset };
  if (side === "right") return { x: right(bounds), y: bounds.y + bounds.height * offset };
  if (side === "top") return { x: bounds.x + bounds.width * offset, y: bounds.y };
  return { x: bounds.x + bounds.width * offset, y: bottom(bounds) };
}

function isRecovery(type: string): boolean {
  return ["retry", "retry_of", "tool_retry", "resumed_from", "tool_recovery", "task_recovery"].includes(type);
}

export function relationPortKind(type: string): RelationPortKind {
  if (type === "sequence") return "sequence";
  if (type === "result_return") return "result";
  return isRecovery(type) ? "recovery" : "structure";
}

function portName(kind: RelationPortKind, side: RelationPortSide, direction: "source" | "target"): string {
  return `${side}-${kind}-${direction}`;
}

export function relationPorts(
  edge: RelationEdgeInput,
  source: RouteNodeBounds,
  target: RouteNodeBounds,
): { sourcePort: RelationPort; targetPort: RelationPort } {
  const kind = relationPortKind(edge.type);
  if (kind === "sequence" || (kind === "structure" && source.laneSide === target.laneSide)) {
    return {
      sourcePort: { id: portName(kind, "bottom", "source"), side: "bottom", point: pointOnSide(source, "bottom", 0.34) },
      targetPort: { id: portName(kind, "top", "target"), side: "top", point: pointOnSide(target, "top", 0.66) },
    };
  }
  const toRight = target.x + target.width / 2 >= source.x + source.width / 2;
  const sourceSide: RelationPortSide = toRight ? "right" : "left";
  const targetSide: RelationPortSide = toRight ? "left" : "right";
  const offset = kind === "structure" ? 0.35 : kind === "result" ? 0.58 : 0.76;
  return {
    sourcePort: { id: portName(kind, sourceSide, "source"), side: sourceSide, point: pointOnSide(source, sourceSide, offset) },
    targetPort: { id: portName(kind, targetSide, "target"), side: targetSide, point: pointOnSide(target, targetSide, offset) },
  };
}

export function buildOrthogonalRoutePath(points: readonly RoutePoint[]): string {
  return points.map((point, index) => `${index ? "L" : "M"} ${point.x} ${point.y}`).join(" ");
}

interface Segment { start: RoutePoint; end: RoutePoint }

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
    return segment.start.y >= top && segment.start.y <= boundsBottom && maxX >= left && minX <= boundsRight;
  }
  if (segment.start.x === segment.end.x) {
    const minY = Math.min(segment.start.y, segment.end.y);
    const maxY = Math.max(segment.start.y, segment.end.y);
    return segment.start.x >= left && segment.start.x <= boundsRight && maxY >= top && minY <= boundsBottom;
  }
  return false;
}

function routeBounds(points: readonly RoutePoint[]): RouteBounds {
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  return { x: Math.min(...xs), y: Math.min(...ys), width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) };
}

function routeLength(points: readonly RoutePoint[]): number {
  return points.slice(1).reduce((total, point, index) => total + Math.abs(point.x - points[index].x) + Math.abs(point.y - points[index].y), 0);
}

function intersectsObstacle(points: readonly RoutePoint[], obstacles: readonly RouteNodeBounds[], clearance: number): boolean {
  return points.slice(1).some((point, index) => obstacles.some((obstacle) => segmentIntersectsBounds({ start: points[index], end: point }, obstacle, clearance)));
}

function localCandidates(
  source: RelationPort,
  target: RelationPort,
  sourceBounds: RouteNodeBounds,
  targetBounds: RouteNodeBounds,
  clearance: number,
): RoutePoint[][] {
  if (["top", "bottom"].includes(source.side) && ["top", "bottom"].includes(target.side)) {
    const corridorY = (source.point.y + target.point.y) / 2;
    return [[source.point, { x: source.point.x, y: corridorY }, { x: target.point.x, y: corridorY }, target.point]];
  }
  const outward = (port: RelationPort, bounds: RouteNodeBounds): RoutePoint => {
    const distance = clearance + 16;
    if (port.side === "left") return { x: bounds.x - distance, y: port.point.y };
    if (port.side === "right") return { x: right(bounds) + distance, y: port.point.y };
    if (port.side === "top") return { x: port.point.x, y: bounds.y - distance };
    return { x: port.point.x, y: bottom(bounds) + distance };
  };
  const sourceEscape = outward(source, sourceBounds);
  const targetEscape = outward(target, targetBounds);
  const middleX = (sourceEscape.x + targetEscape.x) / 2;
  return [[
    source.point,
    sourceEscape,
    { x: middleX, y: sourceEscape.y },
    { x: middleX, y: targetEscape.y },
    targetEscape,
    target.point,
  ]];
}

function obstacleDetour(
  source: RelationPort,
  target: RelationPort,
  sourceBounds: RouteNodeBounds,
  targetBounds: RouteNodeBounds,
  obstacles: readonly RouteNodeBounds[],
  clearance: number,
): RoutePoint[] {
  const local = obstacles.filter((obstacle) => obstacle.x <= Math.max(right(sourceBounds), right(targetBounds)) + 48
    && right(obstacle) >= Math.min(sourceBounds.x, targetBounds.x) - 48);
  const above = Math.min(sourceBounds.y, targetBounds.y, ...local.map((obstacle) => obstacle.y)) - clearance - 20;
  const below = Math.max(bottom(sourceBounds), bottom(targetBounds), ...local.map(bottom)) + clearance + 20;
  const corridorY = Math.abs(above - source.point.y) + Math.abs(above - target.point.y)
    <= Math.abs(below - source.point.y) + Math.abs(below - target.point.y) ? above : below;
  const sourceX = source.side === "left" ? sourceBounds.x - clearance - 16 : right(sourceBounds) + clearance + 16;
  const targetX = target.side === "left" ? targetBounds.x - clearance - 16 : right(targetBounds) + clearance + 16;
  return [source.point, { x: sourceX, y: source.point.y }, { x: sourceX, y: corridorY }, { x: targetX, y: corridorY }, { x: targetX, y: target.point.y }, target.point];
}

export function buildLocalRelationRoutes(input: RelationRouteInput): Map<string, RelationRoute> {
  const byId = new Map(input.nodeBounds.map((bounds) => [bounds.id, bounds]));
  const clearance = input.clearance ?? RELATION_CLEARANCE;
  const routes = new Map<string, RelationRoute>();
  for (const edge of [...input.edges].sort((a, b) => a.id.localeCompare(b.id))) {
    const sourceBounds = byId.get(edge.source);
    const targetBounds = byId.get(edge.target);
    if (!sourceBounds || !targetBounds) continue;
    const ports = relationPorts(edge, sourceBounds, targetBounds);
    const obstacles = input.nodeBounds.filter((bounds) => bounds.id !== edge.source && bounds.id !== edge.target);
    let points = localCandidates(ports.sourcePort, ports.targetPort, sourceBounds, targetBounds, clearance)[0];
    let fallbackReason: string | undefined;
    if (intersectsObstacle(points, obstacles, clearance)) {
      points = obstacleDetour(ports.sourcePort, ports.targetPort, sourceBounds, targetBounds, obstacles, clearance);
      fallbackReason = "local_obstacle_detour";
    }
    const manhattanDistance = Math.abs(ports.sourcePort.point.x - ports.targetPort.point.x) + Math.abs(ports.sourcePort.point.y - ports.targetPort.point.y);
    const length = routeLength(points);
    routes.set(edge.id, {
      edgeId: edge.id,
      points,
      path: buildOrthogonalRoutePath(points),
      bounds: routeBounds(points),
      sourcePort: ports.sourcePort,
      targetPort: ports.targetPort,
      bendCount: Math.max(0, points.length - 2),
      routeLength: length,
      manhattanDistance,
      detourRatio: length / Math.max(manhattanDistance, 1),
      fallbackReason,
    });
  }
  return routes;
}

// Compatibility export for extensions that used the pre-ELK router name.
export const buildRelationRoutes = buildLocalRelationRoutes;
