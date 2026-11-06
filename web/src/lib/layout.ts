import type { TimelineNode, Tree } from "./types";

export const STEP_X = 76;
export const LANE_Y = 64;

export interface Stop {
  node: TimelineNode;
  column: number;
  lane: number;
  isLeaf: boolean;
}

/**
 * Lay the tree out like a track diagram. Each column is a step from the root. A step sits on the
 * same lane as its first child, so a run reads as one straight line and every fork peels off below
 * it. Lanes are handed out depth first, so a fork never crosses another fork's line.
 */
export function layoutTree(tree: Tree): Map<string, Stop> {
  const children = new Map<string, TimelineNode[]>();
  let root: TimelineNode | undefined;
  for (const node of tree.nodes) {
    if (node.parent_id === null) {
      root = node;
      continue;
    }
    const siblings = children.get(node.parent_id);
    if (siblings) siblings.push(node);
    else children.set(node.parent_id, [node]);
  }
  const stops = new Map<string, Stop>();
  if (!root) return stops;

  for (const [parentId, siblings] of children) {
    const parentBranch = tree.nodes.find((node) => node.id === parentId)?.branch_id;
    siblings.sort((a, b) => {
      const continues = Number(b.branch_id === parentBranch) - Number(a.branch_id === parentBranch);
      return continues || a.created_at.localeCompare(b.created_at) || a.seq - b.seq;
    });
  }

  let nextLane = 0;
  const place = (node: TimelineNode, column: number): number => {
    const below = children.get(node.id) ?? [];
    let lane = -1;
    for (const child of below) {
      const childLane = place(child, column + 1);
      if (lane === -1) lane = childLane;
    }
    if (lane === -1) lane = nextLane++;
    stops.set(node.id, { node, column, lane, isLeaf: below.length === 0 });
    return lane;
  };
  place(root, 0);
  return stops;
}

/** A level line, or a curve with level ends that leaves the source and joins the target's lane. */
export function trackPath(sx: number, sy: number, tx: number, ty: number): string {
  if (Math.abs(sy - ty) < 1) return `M ${sx} ${sy} L ${tx} ${ty}`;
  const middle = (sx + tx) / 2;
  return `M ${sx} ${sy} C ${middle} ${sy}, ${middle} ${ty}, ${tx} ${ty}`;
}
