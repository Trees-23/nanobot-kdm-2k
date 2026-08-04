import { describe, expect, it } from "vitest";

import {
  buildLocalRelationRoutes,
  RELATION_CLEARANCE,
  relationPorts,
  SECONDARY_RELATION_TYPES,
  segmentIntersectsBounds,
  STRUCTURAL_RELATION_TYPES,
  type RelationEdgeInput,
  type RouteNodeBounds,
} from "@/components/traces/toolRelationRouting";

const nodes: RouteNodeBounds[] = [
  { id: "main", x: 960, y: 84, width: 248, height: 76, laneSide: "center" },
  { id: "task-left", x: 624, y: 176, width: 248, height: 76, laneSide: "left" },
  { id: "child-left", x: 624, y: 308, width: 248, height: 76, laneSide: "left" },
  { id: "task-right", x: 1296, y: 176, width: 248, height: 76, laneSide: "right" },
  { id: "child-right", x: 1296, y: 308, width: 248, height: 76, laneSide: "right" },
];

function segments(points: readonly { x: number; y: number }[]) {
  return points.slice(1).map((end, index) => ({ start: points[index], end }));
}

describe("local relation routing", () => {
  it("freezes the default relationship hierarchy", () => {
    expect([...STRUCTURAL_RELATION_TYPES].sort()).toEqual(["sequence", "spawn_branch", "task_execution"]);
    expect(SECONDARY_RELATION_TYPES.has("result_return")).toBe(true);
    expect(SECONDARY_RELATION_TYPES.has("tool_recovery")).toBe(true);
    expect(SECONDARY_RELATION_TYPES.has("sequence")).toBe(false);
  });

  it("uses distinct fixed ports for execution, result, and recovery", () => {
    const source = nodes[0];
    const target = nodes[3];
    const structure = relationPorts({ id: "spawn", type: "spawn_branch", source: source.id, target: target.id }, source, target);
    const result = relationPorts({ id: "result", type: "result_return", source: source.id, target: target.id }, source, target);
    const recovery = relationPorts({ id: "recovery", type: "tool_recovery", source: source.id, target: target.id }, source, target);
    expect(structure.sourcePort.id).toBe("right-structure-source");
    expect(result.sourcePort.id).toBe("right-result-source");
    expect(recovery.sourcePort.id).toBe("right-recovery-source");
    expect(new Set([structure.sourcePort.point.y, result.sourcePort.point.y, recovery.sourcePort.point.y]).size).toBe(3);
  });

  it("keeps default structural branches local without global rails", () => {
    const edges: RelationEdgeInput[] = [
      { id: "left-spawn", type: "spawn_branch", source: "main", target: "task-left" },
      { id: "right-spawn", type: "spawn_branch", source: "main", target: "task-right" },
      { id: "left-execution", type: "task_execution", source: "task-left", target: "child-left" },
      { id: "right-execution", type: "task_execution", source: "task-right", target: "child-right" },
    ];
    const routes = buildLocalRelationRoutes({ edges, nodeBounds: nodes });
    for (const edge of edges) {
      const route = routes.get(edge.id)!;
      const source = nodes.find((node) => node.id === edge.source)!;
      const target = nodes.find((node) => node.id === edge.target)!;
      const localLeft = Math.min(source.x, target.x) - 48;
      const localRight = Math.max(source.x + source.width, target.x + target.width) + 48;
      expect(route.points.every((point) => point.x >= localLeft && point.x <= localRight)).toBe(true);
      expect(route.bendCount).toBeLessThanOrEqual(4);
      expect(route.detourRatio).toBeLessThanOrEqual(1.6);
      expect(route.fallbackReason).toBeUndefined();
    }
  });

  it("is deterministic when the backend returns graph edges in a different order", () => {
    const edges: RelationEdgeInput[] = [
      { id: "left", type: "spawn_branch", source: "main", target: "task-left" },
      { id: "right", type: "spawn_branch", source: "main", target: "task-right" },
      { id: "result", type: "result_return", source: "child-left", target: "main" },
    ];
    const first = buildLocalRelationRoutes({ edges, nodeBounds: nodes });
    const second = buildLocalRelationRoutes({ edges: [...edges].reverse(), nodeBounds: nodes });
    expect([...second]).toEqual([...first]);
  });

  it("keeps sequence vertical and exposes route metrics for audit assertions", () => {
    const route = buildLocalRelationRoutes({
      edges: [{ id: "sequence", type: "sequence", source: "task-left", target: "child-left" }],
      nodeBounds: nodes,
    }).get("sequence")!;
    expect(route.sourcePort.id).toBe("bottom-sequence-source");
    expect(route.targetPort.id).toBe("top-sequence-target");
    expect(route.manhattanDistance).toBeGreaterThan(0);
    expect(route.routeLength).toBeGreaterThan(0);
    expect(route.detourRatio).toBeGreaterThanOrEqual(1);
  });

  it("records a local obstacle fallback without expanding to graph-wide bounds", () => {
    const obstacle: RouteNodeBounds = { id: "obstacle", x: 1078, y: 135, width: 120, height: 80, laneSide: "center" };
    const route = buildLocalRelationRoutes({
      edges: [{ id: "blocked", type: "spawn_branch", source: "task-left", target: "task-right" }],
      nodeBounds: [...nodes, obstacle],
    }).get("blocked")!;
    expect(route.fallbackReason).toBe("local_obstacle_detour");
    expect(route.bendCount).toBeLessThanOrEqual(5);
    for (const segment of segments(route.points)) {
      expect(segmentIntersectsBounds(segment, obstacle, RELATION_CLEARANCE)).toBe(false);
    }
  });
});
