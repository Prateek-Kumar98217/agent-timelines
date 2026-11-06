import type { TimelineNode, Tree } from "./types";

export const BLOCK_WIDTH = 284;
export const BLOCK_HEIGHT = 148;
export const STEP_Y = 212;
export const LANE_X = 320;

export interface Stop {
  node: TimelineNode;
  /** Steps from the root, which sets the height on the page. */
  depth: number;
  /** Which vertical line it's on, counted from the left. */
  lane: number;
  isLeaf: boolean;
}

/**
 * Lay the tree out from top to bottom. A step sits on the same lane as its first child, so a run
 * falls in one straight line and every fork steps to the right of it. Lanes are handed out depth
 * first, so a fork never crosses another fork's line.
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
  const place = (node: TimelineNode, depth: number): number => {
    const below = children.get(node.id) ?? [];
    let lane = -1;
    for (const child of below) {
      const childLane = place(child, depth + 1);
      if (lane === -1) lane = childLane;
    }
    if (lane === -1) lane = nextLane++;
    stops.set(node.id, { node, depth, lane, isLeaf: below.length === 0 });
    return lane;
  };
  place(root, 0);
  return stops;
}

const CORNER = 10;

/** Straight down on one lane. Between lanes: down, across, then down, with rounded corners. */
export function arrowPath(sx: number, sy: number, tx: number, ty: number): string {
  if (Math.abs(sx - tx) < 1) return `M ${sx} ${sy} L ${tx} ${ty}`;
  const middle = (sy + ty) / 2;
  const corner = Math.min(CORNER, Math.abs(tx - sx) / 2, (ty - sy) / 2);
  const side = tx > sx ? 1 : -1;
  return [
    `M ${sx} ${sy}`,
    `L ${sx} ${middle - corner}`,
    `Q ${sx} ${middle} ${sx + side * corner} ${middle}`,
    `L ${tx - side * corner} ${middle}`,
    `Q ${tx} ${middle} ${tx} ${middle + corner}`,
    `L ${tx} ${ty}`,
  ].join(" ");
}

/** The arrowhead, pointing down with its tip on the target. */
export function arrowHead(tx: number, ty: number): string {
  return `M ${tx - 6} ${ty - 9} L ${tx} ${ty} L ${tx + 6} ${ty - 9} Z`;
}
