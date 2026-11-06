import { describe, expect, it } from "vitest";
import { layoutTree, trackPath } from "./layout";
import type { TimelineNode, Tree } from "./types";

function node(id: string, parent: string | null, branch: string, seq: number): TimelineNode {
  return {
    id,
    branch_id: branch,
    parent_id: parent,
    edited_from_id: null,
    type: parent === null ? "root" : "llm_output",
    payload: {},
    seq,
    turn_index: 0,
    input_tokens: 0,
    output_tokens: 0,
    created_at: `2026-01-01T00:00:${String(seq).padStart(2, "0")}Z`,
  };
}

const tree = (nodes: TimelineNode[]): Tree => ({
  id: "t",
  branches: [],
  nodes,
  last_event_seq: 0,
});

describe("layoutTree", () => {
  it("keeps a single run on one lane", () => {
    const stops = layoutTree(tree([node("r", null, "m", 0), node("a", "r", "m", 1), node("b", "a", "m", 2)]));
    expect([...stops.values()].map((stop) => stop.lane)).toEqual([0, 0, 0]);
    expect(stops.get("b")).toMatchObject({ column: 2, isLeaf: true });
  });

  it("keeps the source branch straight when a fork was created first in time order", () => {
    // The fork's node is listed first and created earlier, but the source branch keeps lane 0.
    const stops = layoutTree(
      tree([node("r", null, "m", 0), node("f", "r", "fork", 1), node("a", "r", "m", 2)]),
    );
    expect(stops.get("a")!.lane).toBe(0);
    expect(stops.get("f")!.lane).toBe(1);
  });

  it("gives every leaf its own lane so forks never cross", () => {
    const stops = layoutTree(
      tree([
        node("r", null, "m", 0),
        node("a", "r", "m", 1),
        node("b", "a", "m", 2),
        node("c", "b", "m", 3),
        node("x", "b", "f1", 4),
        node("y", "a", "f2", 5),
        node("z", "r", "f3", 6),
      ]),
    );
    const leaves = ["c", "x", "y", "z"].map((id) => stops.get(id)!.lane);
    expect(leaves).toEqual([0, 1, 2, 3]);
  });

  it("copes with an empty tree", () => {
    expect(layoutTree(tree([])).size).toBe(0);
  });
});

describe("trackPath", () => {
  it("draws a straight line on one lane and a curve between lanes", () => {
    expect(trackPath(0, 10, 50, 10)).toBe("M 0 10 L 50 10");
    expect(trackPath(0, 10, 50, 90)).toContain("C 25 10, 25 90, 50 90");
  });
});
