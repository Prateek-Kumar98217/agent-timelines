export type NodeType = "root" | "user_msg" | "llm_output" | "tool_call" | "tool_result";

export type BranchStatus =
  | "queued"
  | "running"
  | "waiting"
  | "completed"
  | "capped"
  | "budget_exhausted"
  | "failed"
  | "cancelled";

export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
}

export interface ToolSpec {
  name: string;
  description: string;
  parameters: {
    required?: string[];
    properties?: Record<string, { type?: string; maxLength?: number }>;
  };
}

export interface TimelineNode {
  id: string;
  branch_id: string;
  parent_id: string | null;
  edited_from_id: string | null;
  type: NodeType;
  // The shape depends on `type`. Read it through the helpers in nodes.ts.
  payload: Record<string, unknown>;
  seq: number;
  turn_index: number;
  input_tokens: number;
  output_tokens: number;
  created_at: string;
}

export interface Branch {
  id: string;
  tree_id: string;
  parent_branch_id: string | null;
  fork_node_id: string | null;
  status: BranchStatus;
  status_reason: string | null;
  turns_used: number;
  extra_turns: number;
  input_tokens: number;
  output_tokens: number;
  created_at: string;
  finished_at: string | null;
}

export interface Tree {
  id: string;
  branches: Branch[];
  nodes: TimelineNode[];
  last_event_seq: number;
}

export interface TreeSummary {
  id: string;
  task: string;
  created_at: string;
  branches: number;
}

export interface Provider {
  name: string;
  needs_key: boolean;
  available: boolean;
  default_model: string;
  key_last_four: string | null;
}

export interface BranchSummary {
  branch_id: string;
  status: BranchStatus;
  turns_used: number;
  input_tokens: number;
  output_tokens: number;
  path_tokens: number;
  final_text: string | null;
  tool_calls: string[];
  scores: Record<string, number>;
}

export interface Comparison {
  a: BranchSummary;
  b: BranchSummary;
  last_common_node_id: string;
  shared_nodes: number;
  same_final_text: boolean;
  tool_call_overlap: number;
  final_similarity: number | null;
  converged: boolean | null;
}

export interface Limits {
  active_branches: number;
  max_active_branches: number;
  max_turns: number;
}
