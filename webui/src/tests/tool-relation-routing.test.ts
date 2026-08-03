import { describe, expect, it } from "vitest";

import {
  buildRelationRoutes,
  RELATION_CLEARANCE,
  RELATION_RAIL_GAP,
  segmentIntersectsBounds,
  ROUTED_RELATION_TYPES,
  type RelationEdgeInput,
  type RouteNodeBounds,
  type RoutePoint,
} from "@/components/traces/toolRelationRouting";

const nodes: RouteNodeBounds[] = [
  { id: "source", x: 100, y: 40, width: 100, height: 40 },
  { id: "middle", x: 100, y: 120, width: 100, height: 40 },
  { id: "target", x: 100, y: 200, width: 100, height: 40 },
];

const edges: RelationEdgeInput[] = [
  { id: "recovery", type: "tool_recovery", source: "source", target: "target" },
  { id: "retry", type: "tool_retry", source: "source", target: "middle" },
  { id: "continuation", type: "tool_continuation", source: "middle", target: "target" },
];

function segments(points: readonly RoutePoint[]) {
  return points.slice(1).map((point, index) => ({ start: points[index], end: point }));
}

describe("cross-lane relation routing", () => {
  it("routes all explicit tool relations through right-side rails outside visible bounds", () => {
    const routes = buildRelationRoutes({ edges, nodeBounds: nodes, rightBoundary: 200 });
    expect([...routes.keys()].sort()).toEqual(["continuation", "recovery", "retry"]);
    for (const edge of edges) {
      const route = routes.get(edge.id)!;
      expect(route.railX).toBeGreaterThanOrEqual(200 + RELATION_RAIL_GAP);
      expect(route.points[0].x).toBe(200);
      expect(route.points.at(-1)!.x).toBe(200);
      expect(route.points.some((point) => point.x === route.railX)).toBe(true);
      expect(route.path).toMatch(/^M /);
    }
  });

  it("assigns overlapping intervals deterministic adjacent slots regardless of edge order", () => {
    const forward = buildRelationRoutes({ edges, nodeBounds: nodes, rightBoundary: 200 });
    const reversed = buildRelationRoutes({ edges: [...edges].reverse(), nodeBounds: nodes, rightBoundary: 200 });
    expect(new Set([...forward.values()].map((route) => route.slot)).size).toBeGreaterThan(1);
    for (const edge of edges) {
      expect(reversed.get(edge.id)).toEqual(forward.get(edge.id));
    }
  });

  it("reuses a slot for vertically separated intervals", () => {
    const separatedNodes = [
      ...nodes,
      { id: "lower-source", x: 100, y: 320, width: 100, height: 40 },
      { id: "lower-target", x: 100, y: 400, width: 100, height: 40 },
    ];
    const routes = buildRelationRoutes({
      edges: [edges[1], { id: "lower", type: "tool_retry", source: "lower-source", target: "lower-target" }],
      nodeBounds: separatedNodes,
      rightBoundary: 200,
    });
    expect(routes.get("retry")!.slot).toBe(routes.get("lower")!.slot);
  });

  it("keeps reversed and same-Y endpoints on valid right-side orthogonal routes", () => {
    const routes = buildRelationRoutes({
      edges: [
        { id: "reversed", type: "tool_recovery", source: "target", target: "source" },
        { id: "same-y", type: "tool_continuation", source: "source", target: "peer" },
      ],
      nodeBounds: [...nodes, { id: "peer", x: 300, y: 40, width: 100, height: 40 }],
      rightBoundary: 400,
    });
    expect(routes.get("reversed")!.points[0].y).toBeGreaterThan(routes.get("reversed")!.points.at(-1)!.y);
    expect(routes.get("same-y")!.points[0].y).toBe(routes.get("same-y")!.points.at(-1)!.y);
    expect(routes.get("same-y")!.railX).toBeGreaterThan(400);
  });

  it("does not create routes for sequence or missing endpoints", () => {
    const routes = buildRelationRoutes({
      edges: [
        { id: "sequence", type: "sequence", source: "source", target: "target" },
        { id: "dangling", type: "tool_retry", source: "source", target: "hidden" },
      ],
      nodeBounds: nodes,
      rightBoundary: 200,
    });
    expect(routes.size).toBe(0);
  });

  it("routes a cross-lane edge around non-endpoint obstacles", () => {
    const crossLaneNodes = [
      { id: "source", x: 100, y: 100, width: 100, height: 40 },
      { id: "blocker", x: 240, y: 80, width: 100, height: 80 },
      { id: "target", x: 100, y: 260, width: 100, height: 40 },
    ];
    const route = buildRelationRoutes({
      edges: [{ id: "cross", type: "tool_recovery", source: "source", target: "target" }],
      nodeBounds: crossLaneNodes,
      rightBoundary: 340,
    }).get("cross")!;
    for (const segment of segments(route.points)) {
      expect(segmentIntersectsBounds(segment, crossLaneNodes[1], 8)).toBe(false);
    }
  });

  it("leaves no horizontal stub inside a close cross-lane obstacle", () => {
    const closeNodes = [
      { id: "source", x: 100, y: 100, width: 100, height: 40 },
      { id: "close-blocker", x: 215, y: 80, width: 100, height: 80 },
      { id: "target", x: 100, y: 260, width: 100, height: 40 },
    ];
    const route = buildRelationRoutes({
      edges: [{ id: "close-cross", type: "tool_recovery", source: "source", target: "target" }],
      nodeBounds: closeNodes,
      rightBoundary: 315,
    }).get("close-cross")!;
    for (const segment of segments(route.points)) {
      expect(segmentIntersectsBounds(segment, closeNodes[1], 8)).toBe(false);
    }
  });

  it("recomputes the rail from the current visible bounding box", () => {
    const expanded = buildRelationRoutes({ edges: [edges[0]], nodeBounds: nodes, rightBoundary: 200 }).get("recovery")!;
    const collapsed = buildRelationRoutes({
      edges: [edges[0]],
      nodeBounds: nodes.filter((node) => node.id !== "middle"),
      rightBoundary: 500,
    }).get("recovery")!;
    expect(collapsed.railX).toBeGreaterThan(expanded.railX);
  });

  it("routes every declared cross-lane relation type and leaves sequence simple", () => {
    const relationEdges = [...ROUTED_RELATION_TYPES].map((type, index) => ({
      id: `${type}-${index}`,
      type,
      source: "source",
      target: "target",
    }));
    const routes = buildRelationRoutes({
      edges: [...relationEdges, { id: "sequence", type: "sequence", source: "source", target: "target" }],
      nodeBounds: nodes,
      leftBoundary: 100,
      rightBoundary: 200,
    });

    expect([...routes.keys()].sort()).toEqual(relationEdges.map((edge) => edge.id).sort());
    expect(routes.has("sequence")).toBe(false);
  });

  it("uses the target child lane side and independent deterministic slots", () => {
    const sidedNodes: RouteNodeBounds[] = [
      { id: "main", x: 500, y: 40, width: 100, height: 40, laneSide: "center" },
      { id: "left-child", x: 100, y: 180, width: 100, height: 40, laneSide: "left" },
      { id: "right-child", x: 900, y: 180, width: 100, height: 40, laneSide: "right" },
    ];
    const relationEdges: RelationEdgeInput[] = [
      { id: "left-spawn", type: "spawn_branch", source: "main", target: "left-child" },
      { id: "right-spawn", type: "spawn_branch", source: "main", target: "right-child" },
      { id: "left-result", type: "result_return", source: "left-child", target: "main" },
      { id: "right-result", type: "result_return", source: "right-child", target: "main" },
    ];
    const forward = buildRelationRoutes({ edges: relationEdges, nodeBounds: sidedNodes });
    const reversed = buildRelationRoutes({ edges: [...relationEdges].reverse(), nodeBounds: sidedNodes });

    expect(forward.get("left-spawn")!.side).toBe("left");
    expect(forward.get("left-result")!.side).toBe("left");
    expect(forward.get("right-spawn")!.side).toBe("right");
    expect(forward.get("right-result")!.side).toBe("right");
    for (const edge of relationEdges) expect(reversed.get(edge.id)).toEqual(forward.get(edge.id));
  });

  it("keeps every orthogonal segment clear of all non-endpoint nodes", () => {
    const obstacleNodes: RouteNodeBounds[] = [
      { id: "source", x: 500, y: 80, width: 100, height: 40, laneSide: "center" },
      { id: "block-a", x: 260, y: 40, width: 100, height: 120, laneSide: "left" },
      { id: "block-b", x: 100, y: 180, width: 100, height: 80, laneSide: "left" },
      { id: "target", x: 500, y: 300, width: 100, height: 40, laneSide: "center" },
    ];
    const route = buildRelationRoutes({
      edges: [{ id: "return", type: "result_return", source: "block-b", target: "target" }],
      nodeBounds: obstacleNodes,
    }).get("return")!;
    const obstacles = obstacleNodes.filter((node) => !["block-b", "target"].includes(node.id));

    for (const segment of segments(route.points)) {
      for (const obstacle of obstacles) {
        expect(segmentIntersectsBounds(segment, obstacle, RELATION_CLEARANCE)).toBe(false);
      }
    }
  });
});
