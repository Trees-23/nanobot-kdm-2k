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

type Position = { id: string; x: number; y: number };

const CENTER_X = 960;
const LANE_PITCH = 356;
const ROW_PITCH = 116;
const LANE_GAP = 72;
const TOP = 84;

function layout(request: LayoutRequest): Position[] {
  const lanes = new Map<string, LayoutNode[]>();
  for (const node of request.nodes) {
    const values = lanes.get(node.laneId) ?? [];
    values.push(node);
    lanes.set(node.laneId, values);
  }
  for (const values of lanes.values()) {
    values.sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
  }

  const positions = new Map<string, Position>();
  const occupiedBottom = new Map<number, number>();
  const laneList = [...lanes.entries()].sort(([, a], [, b]) => {
    const aNode = a[0];
    const bNode = b[0];
    if (aNode.laneOrder === 0 && bNode.laneOrder !== 0) return -1;
    if (bNode.laneOrder === 0 && aNode.laneOrder !== 0) return 1;
    return aNode.order - bNode.order || aNode.laneOrder - bNode.laneOrder;
  });

  for (const [, nodes] of laneList) {
    const first = nodes[0];
    const incoming = request.edges
      .filter((edge) => edge.target === first.id && ["spawn_branch", "task_execution", "result_return"].includes(edge.relation))
      .map((edge) => positions.get(edge.source))
      .find((value): value is Position => Boolean(value));
    const column = first.laneOrder;
    const priorBottom = occupiedBottom.get(column) ?? TOP - LANE_GAP;
    const anchorY = incoming?.y ?? TOP;
    let y = Math.max(TOP, anchorY, priorBottom + LANE_GAP);
    for (const node of nodes) {
      positions.set(node.id, {
        id: node.id,
        x: CENTER_X + node.laneOrder * LANE_PITCH,
        y,
      });
      y += Math.max(ROW_PITCH, node.height + 40);
    }
    occupiedBottom.set(column, y - Math.max(ROW_PITCH, nodes[nodes.length - 1].height + 40) + nodes[nodes.length - 1].height);
  }
  return request.nodes.map((node) => positions.get(node.id) ?? { id: node.id, x: CENTER_X, y: TOP });
}

self.onmessage = (event: MessageEvent<LayoutRequest>) => {
  const request = event.data;
  try {
    self.postMessage({ id: request.id, positions: layout(request) });
  } catch (error) {
    self.postMessage({
      id: request.id,
      positions: request.nodes.map((node, index) => ({
        id: node.id,
        x: CENTER_X + node.laneOrder * LANE_PITCH,
        y: index * ROW_PITCH + TOP,
      })),
      warning: error instanceof Error ? error.message : "layout_failed",
    });
  }
};

export {};
