"use client";

import { type FormEvent, useId, useState } from "react";
import { ApiError, api } from "@/lib/api";
import {
  ACTIVE_STATUSES,
  type BranchInfo,
  callOf,
  contentOf,
  isFinalAnswer,
  isToolError,
  stepTitle,
  taskOf,
  textOf,
  toolLabel,
} from "@/lib/nodes";
import type { TimelineNode, ToolSpec } from "@/lib/types";

function Args({ args }: { args: Record<string, unknown> }) {
  return (
    <div className="mono prose">
      {Object.entries(args).map(([key, value]) => (
        <div key={key}>
          {key}: {typeof value === "string" ? value : JSON.stringify(value)}
        </div>
      ))}
    </div>
  );
}

function NodeBody({ node }: { node: TimelineNode }) {
  const className = `inspector-body ${node.edited_from_id ? "inspector-body-edited" : ""}`;
  switch (node.type) {
    case "root":
      return <div className={`${className} prose`}>{taskOf(node)}</div>;
    case "user_msg":
      return <div className={`${className} prose`}>{textOf(node)}</div>;
    case "llm_output": {
      const call = callOf(node);
      const text = textOf(node);
      return (
        <div className={className}>
          {text && <div className="prose">{text}</div>}
          {call && (
            <>
              <p style={{ margin: text ? "0.6rem 0 0.2rem" : "0 0 0.2rem" }}>
                <b>Calls {toolLabel(call.name)}</b>
              </p>
              <Args args={call.arguments} />
            </>
          )}
        </div>
      );
    }
    case "tool_call":
      return (
        <div className={className}>
          <Args args={callOf(node)?.arguments ?? {}} />
        </div>
      );
    case "tool_result":
      return (
        <div className={className}>
          {isToolError(node) && (
            <p style={{ marginBottom: "0.3rem" }}>
              <b>The tool reported an error.</b>
            </p>
          )}
          <div className="mono prose">{contentOf(node)}</div>
        </div>
      );
  }
}

/** A value from a form input, as the type the tool's schema asks for. */
function parseArgument(type: string | undefined, raw: string): unknown {
  if (type === "number" || type === "integer") {
    return raw.trim() === "" ? raw : Number(raw);
  }
  if (type === "string" || type === undefined) return raw;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function asInput(value: unknown): string {
  if (value === undefined || value === null) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function ToolFields({
  spec,
  values,
  onChange,
}: {
  spec: ToolSpec | undefined;
  values: Record<string, string>;
  onChange: (name: string, value: string) => void;
}) {
  const baseId = useId();
  const properties = Object.entries(spec?.parameters.properties ?? {});
  if (properties.length === 0) return <p className="quiet small">This tool takes no details.</p>;
  return (
    <>
      {properties.map(([name, property]) => (
        <div key={name}>
          <label className="field-label" htmlFor={`${baseId}-${name}`}>
            {name}
          </label>
          <textarea
            id={`${baseId}-${name}`}
            className="field mono"
            style={{ minHeight: "4rem" }}
            value={values[name] ?? ""}
            maxLength={property.maxLength}
            onChange={(event) => onChange(name, event.target.value)}
          />
        </div>
      ))}
    </>
  );
}

function buildArguments(spec: ToolSpec | undefined, values: Record<string, string>) {
  const result: Record<string, unknown> = {};
  for (const [name, property] of Object.entries(spec?.parameters.properties ?? {})) {
    const raw = values[name] ?? "";
    if (raw !== "" || spec?.parameters.required?.includes(name)) {
      result[name] = parseArgument(property.type, raw);
    }
  }
  return result;
}

const AFTER_EDIT: Record<string, string> = {
  user_msg: "The model runs again from your message.",
  tool_call: "The tool runs again with your details, skipping any saved result.",
  tool_result: "No tool runs. The model continues from your result.",
};

function EditForm({
  node,
  tools,
  onForked,
}: {
  node: TimelineNode;
  tools: ToolSpec[];
  onForked: (branchId: string) => void;
}) {
  const textId = useId();
  const call = callOf(node);
  const [text, setText] = useState(
    node.type === "tool_result" ? contentOf(node) : (textOf(node) ?? ""),
  );
  const [isError, setIsError] = useState(isToolError(node));
  const [mode, setMode] = useState<"answer" | "tool">(call ? "tool" : "answer");
  const [toolName, setToolName] = useState(call?.name ?? tools[0]?.name ?? "");
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(call?.arguments ?? {}).map(([k, v]) => [k, asInput(v)])),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const spec = tools.find((tool) => tool.name === toolName);

  function buildEdit(): Record<string, unknown> {
    switch (node.type) {
      case "user_msg":
        return { text };
      case "tool_call":
        return { arguments: buildArguments(spec, values) };
      case "tool_result":
        return { content: text, is_error: isError };
      default:
        return mode === "answer"
          ? { text, tool_call: null }
          : {
              text: text.trim() ? text : null,
              tool_call: { name: toolName, arguments: buildArguments(spec, values) },
            };
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const branch = await api.fork(node.id, buildEdit());
      onForked(branch.id);
    } catch (caught) {
      if (caught instanceof ApiError && caught.retryAfterSeconds && caught.status === 429) {
        setError(`${caught.message} Try again in ${caught.retryAfterSeconds} seconds.`);
      } else {
        setError((caught as Error).message);
      }
    } finally {
      setBusy(false);
    }
  }

  const after =
    node.type === "llm_output"
      ? mode === "answer"
        ? "The new branch ends here, with this as its final answer."
        : "The tool runs, then the model continues."
      : AFTER_EDIT[node.type];

  return (
    <form className="edit-form" onSubmit={submit}>
      <h3>Edit this step in a new branch</h3>
      {node.type === "llm_output" && (
        <fieldset className="choice" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="field-label">The model should</legend>
          <label>
            <input
              type="radio"
              name="llm-mode"
              checked={mode === "answer"}
              onChange={() => setMode("answer")}
            />
            Give a final answer
          </label>
          <label>
            <input
              type="radio"
              name="llm-mode"
              checked={mode === "tool"}
              onChange={() => setMode("tool")}
            />
            Call a tool
          </label>
        </fieldset>
      )}
      {node.type === "llm_output" && mode === "tool" && (
        <label>
          <span className="field-label">Tool</span>
          <select
            className="field"
            value={toolName}
            onChange={(event) => setToolName(event.target.value)}
          >
            {tools.map((tool) => (
              <option key={tool.name} value={tool.name}>
                {toolLabel(tool.name)}
              </option>
            ))}
          </select>
        </label>
      )}
      {(node.type === "tool_call" || (node.type === "llm_output" && mode === "tool")) && (
        <ToolFields
          spec={spec}
          values={values}
          onChange={(name, value) => setValues({ ...values, [name]: value })}
        />
      )}
      {(node.type === "user_msg" ||
        node.type === "tool_result" ||
        (node.type === "llm_output" && mode === "answer")) && (
        <div>
          <label className="field-label" htmlFor={textId}>
            {node.type === "user_msg"
              ? "Message"
              : node.type === "tool_result"
                ? "Result"
                : "Answer"}
          </label>
          <textarea
            id={textId}
            className="field"
            required={node.type !== "tool_result"}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </div>
      )}
      {node.type === "tool_result" && (
        <label className="choice">
          <span>
            <input
              type="checkbox"
              checked={isError}
              onChange={(event) => setIsError(event.target.checked)}
            />{" "}
            Report this to the model as a tool error
          </span>
        </label>
      )}
      <p className="quiet small">{after} The original run stays exactly as it is.</p>
      <div>
        <button className="button button-edit" disabled={busy} type="submit">
          {busy ? "Creating branch" : "Create branch"}
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

interface Props {
  open: boolean;
  node: TimelineNode | null;
  branch: BranchInfo | null;
  path: TimelineNode[];
  tools: ToolSpec[];
  onClose: () => void;
  onSelect: (id: string) => void;
  onForked: (branchId: string) => void;
}

export function Inspector({ open, node, branch, path, tools, onClose, onSelect, onForked }: Props) {
  const position = node ? path.findIndex((step) => step.id === node.id) : -1;
  const previous = position > 0 ? path[position - 1] : undefined;
  const next = position >= 0 ? path[position + 1] : undefined;
  const tokens = node ? node.input_tokens + node.output_tokens : 0;

  return (
    <aside
      className={`sidebar sidebar-right ${open && node ? "sidebar-open" : ""}`}
      aria-label="Selected step"
      inert={!(open && node)}
    >
      {node && (
        <>
          <div className="sidebar-head">
            <h2>{stepTitle(node)}</h2>
            <button className="button" type="button" onClick={onClose}>
              Close step
            </button>
          </div>
          <nav className="inspector-nav" aria-label="Steps on this path">
            <button
              className="button"
              type="button"
              disabled={!previous}
              onClick={() => previous && onSelect(previous.id)}
            >
              Previous step
            </button>
            <button
              className="button"
              type="button"
              disabled={!next}
              onClick={() => next && onSelect(next.id)}
            >
              Next step
            </button>
          </nav>
          <p className="quiet small">
            {node.type === "root"
              ? "Every branch starts here."
              : `${node.turn_index > 0 ? `Turn ${node.turn_index} on ` : "On "}${branch?.name ?? "this branch"}.`}
            {tokens > 0 &&
              ` Used ${node.input_tokens.toLocaleString()} tokens in, ${node.output_tokens.toLocaleString()} out.`}
          </p>
          <NodeBody node={node} />
          {node.edited_from_id && node.type !== "user_msg" && (
            <p className="notice small">
              You edited this step.{" "}
              <button
                className="link-button"
                type="button"
                onClick={() => onSelect(node.edited_from_id!)}
              >
                Show the original step
              </button>
            </p>
          )}
          {branch && isFinalAnswer(node) && ACTIVE_STATUSES.has(branch.branch.status) && (
            <p className="quiet small">This branch is still finishing.</p>
          )}
          {node.type === "root" || node.type === "user_msg" ? (
            <p className="notice small" style={{ marginTop: "1rem" }}>
              {node.type === "root"
                ? "The task can't be changed. To explore another path, edit any later step."
                : "This older branch changed the task, which is no longer possible. It stays here because it ran."}
            </p>
          ) : (
            <EditForm key={node.id} node={node} tools={tools} onForked={onForked} />
          )}
        </>
      )}
    </aside>
  );
}
