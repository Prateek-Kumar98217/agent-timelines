import type { Branch, BranchStatus, TimelineNode, ToolCall, ToolSpec, Tree } from "./types";

export const ACTIVE_STATUSES: ReadonlySet<BranchStatus> = new Set(["queued", "running", "waiting"]);

export const STATUS_LABELS: Record<BranchStatus, string> = {
  queued: "Queued",
  running: "Running",
  waiting: "Waiting for capacity",
  completed: "Finished",
  capped: "Hit the turn limit",
  budget_exhausted: "Out of tokens",
  failed: "Failed",
  cancelled: "Cancelled",
};

const TOOL_LABELS: Record<string, string> = {
  web_search: "web search",
  page_fetch: "open page",
  kb_search: "KB search",
  calculator: "calculator",
  scratchpad_write: "save note",
  scratchpad_search: "find note",
};

export const toolLabel = (name: string): string => TOOL_LABELS[name] ?? name;

export const taskOf = (node: TimelineNode): string => String(node.payload.task ?? "");
export const textOf = (node: TimelineNode): string | null =>
  typeof node.payload.text === "string" ? node.payload.text : null;
export const contentOf = (node: TimelineNode): string => String(node.payload.content ?? "");
export const isToolError = (node: TimelineNode): boolean => node.payload.is_error === true;

export function callOf(node: TimelineNode): ToolCall | null {
  const value = node.type === "tool_call" ? node.payload : node.payload.tool_call;
  const call = value as Partial<ToolCall> | null | undefined;
  return call && typeof call.name === "string" ? (call as ToolCall) : null;
}

export const isFinalAnswer = (node: TimelineNode): boolean =>
  node.type === "llm_output" && callOf(node) === null;

export function toolsOf(tree: Tree): ToolSpec[] {
  const root = tree.nodes.find((node) => node.parent_id === null);
  const config = root?.payload.config as { tools?: ToolSpec[] } | undefined;
  return config?.tools ?? [];
}

export const taskOfTree = (tree: Tree): string => {
  const root = tree.nodes.find((node) => node.parent_id === null);
  return root ? taskOf(root) : "";
};

export function stepTitle(node: TimelineNode): string {
  switch (node.type) {
    case "root":
      return "Task";
    case "user_msg":
      return "Your message";
    case "llm_output":
      return isFinalAnswer(node) ? "Final answer" : "Model's decision";
    case "tool_call":
      return `Tool call: ${toolLabel(String(node.payload.name))}`;
    case "tool_result":
      return `Tool result: ${toolLabel(String(node.payload.name))}`;
  }
}

const EDIT_LABELS: Record<TimelineNode["type"], string> = {
  root: "",
  user_msg: "your message",
  llm_output: "the model's decision",
  tool_call: "a tool call",
  tool_result: "a tool result",
};

export interface BranchInfo {
  branch: Branch;
  name: string;
  /** What this branch changed, in plain words. */
  change: string;
  lastNode: TimelineNode | null;
  finalText: string | null;
}

/** Name branches by creation order and work out what each one changed. */
export function describeBranches(tree: Tree): BranchInfo[] {
  const ordered = [...tree.branches].sort((a, b) => a.created_at.localeCompare(b.created_at));
  let forkNumber = 0;
  return ordered.map((branch) => {
    const own = tree.nodes.filter((node) => node.branch_id === branch.id);
    const last = own.reduce<TimelineNode | null>(
      (latest, node) => (latest === null || node.seq > latest.seq ? node : latest),
      null,
    );
    const edited = own.find((node) => node.edited_from_id !== null);
    const isMain = branch.parent_branch_id === null;
    if (!isMain) forkNumber += 1;
    return {
      branch,
      name: isMain ? "Original run" : `Branch ${forkNumber}`,
      change: edited
        ? `Edited ${EDIT_LABELS[edited.type]}${edited.turn_index > 0 ? ` in turn ${edited.turn_index}` : ""}`
        : "Nothing edited",
      lastNode: last,
      finalText: last && isFinalAnswer(last) ? textOf(last) : null,
    };
  });
}

/** The nodes from the root to `nodeId`, inclusive. */
export function pathTo(tree: Tree, nodeId: string): TimelineNode[] {
  const byId = new Map(tree.nodes.map((node) => [node.id, node]));
  const path: TimelineNode[] = [];
  let current = byId.get(nodeId);
  while (current) {
    path.push(current);
    current = current.parent_id ? byId.get(current.parent_id) : undefined;
  }
  return path.reverse();
}
