export const TOOL_RELATION_RAIL_GAP = 48;
export const TOOL_RELATION_SLOT_GAP = 12;
export const TOOL_RELATION_CLEARANCE = 8;
export const TOOL_RELATION_INTERVAL_GAP = 8;

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
}

export interface ToolRelationEdgeInput {
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

export interface ToolRelationRoute {
  edgeId: string;
  slot: number;
  railX: number;
  points: readonly RoutePoint[];
  path: string;
  bounds: RouteBounds;
}

export interface ToolRelationRouteInput {
  edges: readonly ToolRelationEdgeInput[];
  nodeBounds: readonly RouteNodeBounds[];
  rightBoundary: number;
  railGap?: number;
  slotGap?: number;
  clearance?: number;
  intervalGap?: number;
}

interface Segment {
  start: RoutePoint;
  end: RoutePoint;
}

interface RoutedEdge extends ToolRelationEdgeInput {
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

function horizontalIntersects(
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

function routePoints(
  edge: RoutedEdge,
  railX: number,
  nodeBounds: readonly RouteNodeBounds[],
  clearance: number,
): RoutePoint[] {
  const obstacles = nodeBounds.filter(
    (bounds) => bounds.id !== edge.source && bounds.id !== edge.target,
  );
  const points: RoutePoint[] = [edge.sourcePoint];
  const sourceHorizontal = horizontalIntersects(edge.sourcePoint, {
    x: railX,
    y: edge.sourcePoint.y,
  }, obstacles, clearance);
  if (sourceHorizontal && obstacles.length) {
    const stubX = edge.sourcePoint.x + clearance * 2;
    appendPoint(points, { x: stubX, y: edge.sourcePoint.y });
    appendPoint(points, { x: stubX, y: corridorY(edge.sourcePoint.y, obstacles, clearance) });
    appendPoint(points, { x: railX, y: points.at(-1)!.y });
  } else {
    appendPoint(points, { x: railX, y: edge.sourcePoint.y });
  }

  const targetHorizontal = horizontalIntersects({ x: railX, y: edge.targetPoint.y }, edge.targetPoint, obstacles, clearance);
  if (targetHorizontal && obstacles.length) {
    const targetCorridorY = corridorY(edge.targetPoint.y, obstacles, clearance);
    const stubX = edge.targetPoint.x + clearance * 2;
    appendPoint(points, { x: railX, y: targetCorridorY });
    appendPoint(points, { x: stubX, y: targetCorridorY });
    appendPoint(points, { x: stubX, y: edge.targetPoint.y });
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

export function buildToolRelationRoutes(input: ToolRelationRouteInput): Map<string, ToolRelationRoute> {
  const byId = new Map(input.nodeBounds.map((bounds) => [bounds.id, bounds]));
  const routedEdges = input.edges.flatMap<RoutedEdge>((edge) => {
    if (!edge.type.startsWith("tool_")) return [];
    const sourceBounds = byId.get(edge.source);
    const targetBounds = byId.get(edge.target);
    if (!sourceBounds || !targetBounds) return [];
    const sourcePoint = { x: right(sourceBounds), y: sourceBounds.y + sourceBounds.height / 2 };
    const targetPoint = { x: right(targetBounds), y: targetBounds.y + targetBounds.height / 2 };
    return [{
      ...edge,
      sourceBounds,
      targetBounds,
      sourcePoint,
      targetPoint,
      startY: Math.min(sourcePoint.y, targetPoint.y),
      endY: Math.max(sourcePoint.y, targetPoint.y),
    }];
  }).sort((a, b) => a.startY - b.startY
    || a.endY - b.endY
    || a.type.localeCompare(b.type)
    || a.id.localeCompare(b.id));

  const intervalGap = input.intervalGap ?? TOOL_RELATION_INTERVAL_GAP;
  const slots: Array<Array<readonly [number, number]>> = [];
  const assignments = new Map<string, number>();
  for (const edge of routedEdges) {
    const interval = [edge.startY, edge.endY] as const;
    let slot = slots.findIndex((intervals) => intervals.every(
      (occupied) => !overlapsInterval(interval, occupied, intervalGap),
    ));
    if (slot === -1) {
      slot = slots.length;
      slots.push([]);
    }
    slots[slot].push(interval);
    assignments.set(edge.id, slot);
  }

  const railGap = input.railGap ?? TOOL_RELATION_RAIL_GAP;
  const slotGap = input.slotGap ?? TOOL_RELATION_SLOT_GAP;
  const clearance = input.clearance ?? TOOL_RELATION_CLEARANCE;
  const visibleRight = Math.max(input.rightBoundary, ...input.nodeBounds.map(right));
  const routes = new Map<string, ToolRelationRoute>();
  for (const edge of routedEdges) {
    const slot = assignments.get(edge.id)!;
    const railX = visibleRight + railGap + slot * slotGap;
    const points = routePoints(edge, railX, input.nodeBounds, clearance);
    routes.set(edge.id, {
      edgeId: edge.id,
      slot,
      railX,
      points,
      path: buildOrthogonalRoutePath(points),
      bounds: routeBounds(points),
    });
  }
  return routes;
}
