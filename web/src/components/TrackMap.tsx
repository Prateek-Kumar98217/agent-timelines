"use client";

import {
  Background,
  BackgroundVariant,
  BaseEdge,
  Controls,
  type Edge,
  type EdgeProps,
  Handle,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import { memo, useMemo } from "react";
import { LANE_Y, STEP_X, layoutTree, trackPath } from "@/lib/layout";
import { ACTIVE_STATUSES, type BranchInfo, STATUS_LABELS, callOf, isFinalAnswer, stepTitle, toolLabel } from "@/lib/nodes";
import type { TimelineNode, Tree } from "@/lib/types";

const STOP_SIZE = 34;
const TERMINUS_WIDTH = 240;

function Glyph({ node }: { node: TimelineNode }) {
  const center = STOP_SIZE / 2;
  const ring = <circle className="stop-ring" cx={center} cy={center} r={15} />;
  const shape = (() => {
    switch (node.type) {
      case "root":
        return <circle className="stop-glyph" cx={center} cy={center} r={10} />;
      case "user_msg":
        return <circle className="stop-glyph stop-glyph-hollow" cx={center} cy={center} r={7} />;
      case "llm_output":
        return isFinalAnswer(node) ? (
          <rect className="stop-glyph" x={center - 3} y={center - 12} width={6} height={24} />
        ) : (
          <circle className="stop-glyph" cx={center} cy={center} r={7} />
        );
      case "tool_call":
        return <rect className="stop-glyph" x={center - 6} y={center - 6} width={12} height={12} />;
      case "tool_result":
        return (
          <rect
            className="stop-glyph stop-glyph-hollow"
            x={center - 6}
            y={center - 6}
            width={12}
            height={12}
          />
        );
    }
  })();
  return (
    <svg width={STOP_SIZE} height={STOP_SIZE} aria-hidden="true">
      {ring}
      {shape}
    </svg>
  );
}

function stopLabel(node: TimelineNode): string | null {
  if (node.type === "root") return "Task";
  if (node.type === "tool_call") return toolLabel(String(callOf(node)?.name));
  if (isFinalAnswer(node)) return "Answer";
  return null;
}

type StopData = {
  node: TimelineNode;
  selected: boolean;
  dim: boolean;
  onSelect: (id: string) => void;
};
type TerminusData = {
  info: BranchInfo;
  selected: boolean;
  onSelect: (id: string) => void;
};

const StopNode = memo(function StopNode({ data }: NodeProps<Node<StopData, "stop">>) {
  const { node, selected, dim, onSelect } = data;
  const edited = node.edited_from_id !== null;
  const classes = ["stop", selected && "stop-selected", dim && "stop-dim", edited && "stop-edited"]
    .filter(Boolean)
    .join(" ");
  const label = stopLabel(node);
  return (
    <>
      <Handle className="flow-handle" type="target" position={Position.Left} isConnectable={false} />
      <button
        className={`${classes} nodrag nopan`}
        type="button"
        aria-pressed={selected}
        aria-label={`${stepTitle(node)}, turn ${node.turn_index}${edited ? ", edited" : ""}`}
        title={stepTitle(node)}
        onClick={() => onSelect(node.id)}
      >
        <Glyph node={node} />
        {label && <span className="stop-label">{label}</span>}
      </button>
      <Handle className="flow-handle" type="source" position={Position.Right} isConnectable={false} />
    </>
  );
});

const TerminusNode = memo(function TerminusNode({ data }: NodeProps<Node<TerminusData, "terminus">>) {
  const { info, selected, onSelect } = data;
  return (
    <button
      className={`terminus nodrag nopan ${selected ? "terminus-selected" : ""}`}
      type="button"
      aria-pressed={selected}
      onClick={() => onSelect(info.branch.id)}
    >
      <i
        className={`lamp lamp-${info.branch.status}`}
        role="img"
        aria-label={STATUS_LABELS[info.branch.status]}
      />
      <span>
        <b>{info.name}</b>
        <span>{ACTIVE_STATUSES.has(info.branch.status) ? STATUS_LABELS[info.branch.status] : info.change}</span>
      </span>
    </button>
  );
});

function TrackEdge({ sourceX, sourceY, targetX, targetY, data }: EdgeProps<Edge<{ kind: string }>>) {
  return (
    <BaseEdge
      className={`track-line ${data?.kind ?? ""}`}
      path={trackPath(sourceX, sourceY, targetX, targetY)}
    />
  );
}

const nodeTypes = { stop: StopNode, terminus: TerminusNode };
const edgeTypes = { track: TrackEdge };

interface Props {
  tree: Tree;
  branches: BranchInfo[];
  selectedNodeId: string | null;
  selectedBranchId: string | null;
  highlighted: ReadonlySet<string>;
  onSelectNode: (id: string) => void;
  onSelectBranch: (id: string) => void;
}

export function TrackMap({
  tree,
  branches,
  selectedNodeId,
  selectedBranchId,
  highlighted,
  onSelectNode,
  onSelectBranch,
}: Props) {
  const { nodes, edges } = useMemo(() => {
    const stops = layoutTree(tree);
    const flowNodes: Node[] = [];
    const flowEdges: Edge[] = [];
    for (const stop of stops.values()) {
      const { node } = stop;
      flowNodes.push({
        id: node.id,
        type: "stop",
        position: { x: stop.column * STEP_X, y: stop.lane * LANE_Y },
        width: STOP_SIZE,
        height: STOP_SIZE,
        draggable: false,
        selectable: false,
        data: {
          node,
          selected: node.id === selectedNodeId,
          dim: !highlighted.has(node.id),
          onSelect: onSelectNode,
        },
      });
      if (node.parent_id) {
        const kind = !highlighted.has(node.id)
          ? "track-line-dim"
          : node.edited_from_id
            ? "track-line-edit"
            : "";
        flowEdges.push({
          id: `${node.parent_id}-${node.id}`,
          source: node.parent_id,
          target: node.id,
          type: "track",
          selectable: false,
          focusable: false,
          data: { kind },
        });
      }
    }
    for (const info of branches) {
      const stop = info.lastNode && stops.get(info.lastNode.id);
      if (!stop) continue;
      flowNodes.push({
        id: `terminus-${info.branch.id}`,
        type: "terminus",
        position: {
          x: stop.column * STEP_X + STOP_SIZE + 10,
          y: stop.lane * LANE_Y,
        },
        width: TERMINUS_WIDTH,
        height: STOP_SIZE,
        draggable: false,
        selectable: false,
        data: {
          info,
          selected: info.branch.id === selectedBranchId,
          onSelect: onSelectBranch,
        },
      });
    }
    return { nodes: flowNodes, edges: flowEdges };
  }, [tree, branches, selectedNodeId, selectedBranchId, highlighted, onSelectNode, onSelectBranch]);

  return (
    <div className="map">
      <div className="legend" aria-hidden="true">
        <span>
          <svg viewBox="0 0 14 14"><circle cx="7" cy="7" r="5" fill="var(--enamel)" stroke="var(--track)" strokeWidth="2.5" /></svg>
          You
        </span>
        <span>
          <svg viewBox="0 0 14 14"><circle cx="7" cy="7" r="6" fill="var(--track)" /></svg>
          Model
        </span>
        <span>
          <svg viewBox="0 0 14 14"><rect x="2" y="2" width="10" height="10" fill="var(--track)" /></svg>
          Tool call
        </span>
        <span>
          <svg viewBox="0 0 14 14"><rect x="2" y="2" width="10" height="10" fill="var(--enamel)" stroke="var(--track)" strokeWidth="2.5" /></svg>
          Tool result
        </span>
        <span>
          <svg viewBox="0 0 14 14"><circle cx="7" cy="7" r="6" fill="var(--edit)" /></svg>
          Edited
        </span>
      </div>
      <div className="map-canvas">
      <ReactFlow
        fitView
        fitViewOptions={{ padding: 0.06, maxZoom: 1 }}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        minZoom={0.25}
        maxZoom={1.5}
      >
        <Background variant={BackgroundVariant.Dots} gap={STEP_X} size={1.5} color="#b4c6bf" />
        <Controls showInteractive={false} />
      </ReactFlow>
      </div>
    </div>
  );
}
