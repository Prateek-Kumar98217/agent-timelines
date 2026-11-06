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
  task_search: "search this task",
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

/** A tree keeps the turn limit it started with, so this can differ from today's default. */
export function turnLimitOf(tree: Tree): number | null {
  const root = tree.nodes.find((node) => node.parent_id === null);
  const config = root?.payload.config as { turn_cap?: number } | undefined;
  return config?.turn_cap ?? null;
}

/** Whether a branch has finished by using every turn it had, so it can take another message. */
export function canContinue(branch: Branch, baseTurnLimit: number | null): boolean {
  if (baseTurnLimit === null) return false;
  if (branch.status !== "completed" && branch.status !== "capped") return false;
  return branch.turns_used >= baseTurnLimit + branch.extra_turns;
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

export interface BlockInfo {
  /** The tag's name, and the class suffix that colours it. */
  tag: string;
  kind: "task" | "you" | "model" | "call" | "result" | "answer";
  /** A short bold line, such as the tool's name. */
  heading: string | null;
  /** The text shown on the block, which the block clamps to fit. */
  summary: string;
}

const argumentsText = (call: ToolCall | null): string =>
  Object.values(call?.arguments ?? {})
    .map((value) => (typeof value === "string" ? value : JSON.stringify(value)))
    .join(", ");

/** What a step's block shows without being opened. */
export function blockInfo(node: TimelineNode): BlockInfo {
  switch (node.type) {
    case "root":
      return { tag: "Task", kind: "task", heading: null, summary: taskOf(node) };
    case "user_msg":
      return { tag: "You", kind: "you", heading: null, summary: textOf(node) ?? "" };
    case "tool_call": {
      const call = callOf(node);
      return {
        tag: "Tool call",
        kind: "call",
        heading: toolLabel(String(call?.name)),
        summary: argumentsText(call),
      };
    }
    case "tool_result":
      return {
        tag: "Tool result",
        kind: "result",
        heading: toolLabel(String(node.payload.name)),
        summary: contentOf(node),
      };
    case "llm_output": {
      const call = callOf(node);
      if (!call) return { tag: "Answer", kind: "answer", heading: null, summary: textOf(node) ?? "" };
      return {
        tag: "Model",
        kind: "model",
        heading: `Decides on ${toolLabel(call.name)}`,
        summary: textOf(node) ?? argumentsText(call),
      };
    }
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

/**
 * Every tree stores its task twice: on the root, and as the first message the model reads. They're
 * the same thing, so the map shows only the root. Older branches that edited that message stay
 * visible, because what ran is never hidden.
 */
export function withoutTaskCopy(tree: Tree): Tree {
  const rootId = tree.nodes.find((node) => node.parent_id === null)?.id;
  const copies = new Map(
    tree.nodes
      .filter(
        (node) =>
          node.type === "user_msg" && node.edited_from_id === null && node.parent_id === rootId,
      )
      .map((node) => [node.id, node.parent_id]),
  );
  return {
    ...tree,
    nodes: tree.nodes
      .filter((node) => !copies.has(node.id))
      .map((node) =>
        node.parent_id && copies.has(node.parent_id)
          ? { ...node, parent_id: copies.get(node.parent_id) ?? null }
          : node,
      ),
  };
}

/** Only an older branch can show this: a message at the top of the tree, which changed the task. */
export function changedTheTask(tree: Tree, node: TimelineNode): boolean {
  if (node.type !== "user_msg") return false;
  return tree.nodes.find((entry) => entry.id === node.parent_id)?.parent_id === null;
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
