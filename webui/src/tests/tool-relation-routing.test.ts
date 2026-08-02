import { describe, expect, it } from "vitest";

import {
  buildToolRelationRoutes,
  segmentIntersectsBounds,
  TOOL_RELATION_RAIL_GAP,
  type RouteNodeBounds,
  type RoutePoint,
  type ToolRelationEdgeInput,
} from "@/components/traces/toolRelationRouting";

const nodes: RouteNodeBounds[] = [
  { id: "source", x: 100, y: 40, width: 100, height: 40 },
  { id: "middle", x: 100, y: 120, width: 100, height: 40 },
  { id: "target", x: 100, y: 200, width: 100, height: 40 },
];

const edges: ToolRelationEdgeInput[] = [
  { id: "recovery", type: "tool_recovery", source: "source", target: "target" },
  { id: "retry", type: "tool_retry", source: "source", target: "middle" },
  { id: "continuation", type: "tool_continuation", source: "middle", target: "target" },
];

function segments(points: readonly RoutePoint[]) {
  return points.slice(1).map((point, index) => ({ start: points[index], end: point }));
}

describe("tool relation routing", () => {
  it("routes all explicit tool relations through right-side rails outside visible bounds", () => {
    const routes = buildToolRelationRoutes({ edges, nodeBounds: nodes, rightBoundary: 200 });
    expect([...routes.keys()].sort()).toEqual(["continuation", "recovery", "retry"]);
    for (const edge of edges) {
      const route = routes.get(edge.id)!;
      expect(route.railX).toBeGreaterThanOrEqual(200 + TOOL_RELATION_RAIL_GAP);
      expect(route.points[0].x).toBe(200);
      expect(route.points.at(-1)!.x).toBe(200);
      expect(route.points.some((point) => point.x === route.railX)).toBe(true);
      expect(route.path).toMatch(/^M /);
    }
  });

  it("assigns overlapping intervals deterministic adjacent slots regardless of edge order", () => {
    const forward = buildToolRelationRoutes({ edges, nodeBounds: nodes, rightBoundary: 200 });
    const reversed = buildToolRelationRoutes({ edges: [...edges].reverse(), nodeBounds: nodes, rightBoundary: 200 });
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
    const routes = buildToolRelationRoutes({
      edges: [edges[1], { id: "lower", type: "tool_retry", source: "lower-source", target: "lower-target" }],
      nodeBounds: separatedNodes,
      rightBoundary: 200,
    });
    expect(routes.get("retry")!.slot).toBe(routes.get("lower")!.slot);
  });

  it("keeps reversed and same-Y endpoints on valid right-side orthogonal routes", () => {
    const routes = buildToolRelationRoutes({
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
    const routes = buildToolRelationRoutes({
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
    const route = buildToolRelationRoutes({
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
    const route = buildToolRelationRoutes({
      edges: [{ id: "close-cross", type: "tool_recovery", source: "source", target: "target" }],
      nodeBounds: closeNodes,
      rightBoundary: 315,
    }).get("close-cross")!;
    for (const segment of segments(route.points)) {
      expect(segmentIntersectsBounds(segment, closeNodes[1], 8)).toBe(false);
    }
  });

  it("recomputes the rail from the current visible bounding box", () => {
    const expanded = buildToolRelationRoutes({ edges: [edges[0]], nodeBounds: nodes, rightBoundary: 200 }).get("recovery")!;
    const collapsed = buildToolRelationRoutes({
      edges: [edges[0]],
      nodeBounds: nodes.filter((node) => node.id !== "middle"),
      rightBoundary: 500,
    }).get("recovery")!;
    expect(collapsed.railX).toBeGreaterThan(expanded.railX);
  });
});
