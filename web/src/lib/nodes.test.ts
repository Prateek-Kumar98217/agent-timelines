import { describe, expect, it } from "vitest";
import { blockInfo, withoutTaskCopy } from "./nodes";
import type { TimelineNode } from "./types";

function node(type: TimelineNode["type"], payload: Record<string, unknown>): TimelineNode {
  return {
    id: "n",
    branch_id: "b",
    parent_id: "p",
    edited_from_id: null,
    type,
    payload,
    seq: 1,
    turn_index: 1,
    input_tokens: 0,
    output_tokens: 0,
    created_at: "2026-01-01T00:00:00Z",
  };
}

describe("withoutTaskCopy", () => {
  const at = (id: string, parent: string | null, type: TimelineNode["type"], edited: string | null = null) => ({
    ...node(type, {}),
    id,
    parent_id: parent,
    edited_from_id: edited,
  });
  const tree = (nodes: TimelineNode[]) => ({ id: "t", branches: [], nodes, last_event_seq: 0 });

  it("hides the first message and joins the next step to the task", () => {
    const shown = withoutTaskCopy(
      tree([at("r", null, "root"), at("u", "r", "user_msg"), at("m", "u", "llm_output")]),
    );
    expect(shown.nodes.map((entry) => [entry.id, entry.parent_id])).toEqual([
      ["r", null],
      ["m", "r"],
    ]);
  });

  it("keeps older branches that edited the message", () => {
    const shown = withoutTaskCopy(
      tree([at("r", null, "root"), at("u", "r", "user_msg"), at("e", "r", "user_msg", "u")]),
    );
    expect(shown.nodes.map((entry) => entry.id)).toEqual(["r", "e"]);
  });
});

describe("blockInfo", () => {
  it("gives every step type its own tag", () => {
    const call = { name: "kb_search", arguments: { query: "Apollo" } };
    const tags = [
      node("root", { task: "t" }),
      node("user_msg", { text: "m" }),
      node("llm_output", { text: null, tool_call: call }),
      node("tool_call", call),
      node("tool_result", { name: "kb_search", content: "c", is_error: false }),
      node("llm_output", { text: "done", tool_call: null }),
    ].map((entry) => blockInfo(entry).tag);
    expect(tags).toEqual(["Task", "You", "Model", "Tool call", "Tool result", "Answer"]);
  });

  it("shows the tool and its arguments on a call", () => {
    const info = blockInfo(node("tool_call", { name: "calculator", arguments: { expression: "1+1" } }));
    expect(info).toMatchObject({ heading: "calculator", summary: "1+1" });
  });

  it("shows what the model decided when it wrote no text", () => {
    const info = blockInfo(
      node("llm_output", { text: null, tool_call: { name: "kb_search", arguments: { query: "Fermi" } } }),
    );
    expect(info).toMatchObject({ heading: "Decides on KB search", summary: "Fermi" });
  });
});
