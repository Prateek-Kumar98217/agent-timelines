"use client";

import {
  Background,
  BackgroundVariant,
  ControlButton,
  Controls,
  type Edge,
  type EdgeProps,
  Handle,
  MiniMap,
  type Node,
  type NodeHandle,
  type NodeProps,
  Position,
  ReactFlow,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import { memo, useEffect, useMemo, useRef } from "react";
import { ARROW_DRAW, ARROW_HEAD, BLOCK_POP, ENTRANCE_MS, timelineNow, useEntrance } from "@/lib/entrance";
import { BLOCK_HEIGHT, BLOCK_WIDTH, LANE_X, STEP_Y, arrowHead, arrowPath, layoutTree } from "@/lib/layout";
import {
  ACTIVE_STATUSES,
  type BlockInfo,
  type BranchInfo,
  STATUS_LABELS,
  blockInfo,
  isToolError,
  stepTitle,
} from "@/lib/nodes";
import type { TimelineNode, Tree } from "@/lib/types";
import type { Arrivals } from "@/lib/useTree";

const TERMINUS_HEIGHT = 44;
const TERMINUS_GAP = 10;
const MIN_READABLE_ZOOM = 0.7;
const MIN_ZOOM = 0.15;
const VIEW_MARGIN = 28;
const PANEL_WIDTH = 432;

/**
 * Fixed handle positions, so React Flow never needs to measure them. Nodes are rebuilt on every
 * update, which discards anything it measured, and an arrow can't be drawn until its handles are
 * known. Without these, arrows were rebuilt on every click and could go missing altogether.
 */
const BLOCK_HANDLES: NodeHandle[] = [
  { type: "target", position: Position.Top, x: BLOCK_WIDTH / 2, y: 0, width: 1, height: 1 },
  { type: "source", position: Position.Bottom, x: BLOCK_WIDTH / 2, y: BLOCK_HEIGHT, width: 1, height: 1 },
];

type BlockData = {
  node: TimelineNode;
  selected: boolean;
  dim: boolean;
  entranceAt: number | undefined;
  onSelect: (id: string) => void;
};
type TerminusData = {
  info: BranchInfo;
  selected: boolean;
  onSelect: (id: string) => void;
};

const BlockNode = memo(function BlockNode({ data }: NodeProps<Node<BlockData, "block">>) {
  const { node, selected, dim, entranceAt, onSelect } = data;
  const info: BlockInfo = blockInfo(node);
  const edited = node.edited_from_id !== null;
  const tokens = node.input_tokens + node.output_tokens;
  const element = useRef<HTMLButtonElement>(null);
  useEntrance(element, node.id, entranceAt, BLOCK_POP);
  const classes = [
    "block",
    `block-${info.kind}`,
    selected && "block-selected",
    dim && "block-dim",
    edited && "block-edited",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <>
      <Handle className="flow-handle" type="target" position={Position.Top} isConnectable={false} />
      <button
        ref={element}
        className={`${classes} nodrag nopan`}
        data-step={node.id}
        type="button"
        aria-pressed={selected}
        aria-label={`${stepTitle(node)}, turn ${node.turn_index}${edited ? ", edited" : ""}`}
        onClick={() => onSelect(node.id)}
      >
        <span className="block-head">
          <span className="tag">{info.tag}</span>
          {edited && <span className="tag tag-edited">Edited</span>}
          {isToolError(node) && <span className="tag tag-error">Error</span>}
          {node.turn_index > 0 && <span className="block-turn">Turn {node.turn_index}</span>}
        </span>
        {info.heading && <span className="block-heading">{info.heading}</span>}
        <span className={`block-summary ${info.heading ? "block-summary-short" : ""}`}>
          {info.summary}
        </span>
        {(info.kind === "model" || info.kind === "answer") && (
          <span className="block-tokens">{tokens.toLocaleString()} tokens</span>
        )}
      </button>
      <Handle className="flow-handle" type="source" position={Position.Bottom} isConnectable={false} />
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
      <i className={`lamp lamp-${info.branch.status}`} aria-hidden="true" />
      <span className="terminus-text">
        <b>{info.name}</b>
        <span>
          {ACTIVE_STATUSES.has(info.branch.status) || info.branch.status !== "completed"
            ? STATUS_LABELS[info.branch.status]
            : info.change}
        </span>
      </span>
    </button>
  );
});

type ArrowData = { kind: string; entranceAt: number | undefined };

function ArrowEdge({ id, sourceX, sourceY, targetX, targetY, data }: EdgeProps<Edge<ArrowData>>) {
  const line = useRef<SVGPathElement>(null);
  const head = useRef<SVGPathElement>(null);
  useEntrance(line, id, data?.entranceAt, ARROW_DRAW);
  useEntrance(head, id, data?.entranceAt, ARROW_HEAD);
  return (
    <g className={`arrow ${data?.kind ?? ""}`} data-arrow={id}>
      <path
        ref={line}
        className="arrow-line"
        pathLength={1}
        d={arrowPath(sourceX, sourceY, targetX, targetY - 8)}
      />
      <path ref={head} className="arrow-head" d={arrowHead(targetX, targetY)} />
    </g>
  );
}

const nodeTypes = { block: BlockNode, terminus: TerminusNode };
const edgeTypes = { arrow: ArrowEdge };

/** Opens at a size people can read, and keeps new steps on the selected branch in view. */
function ViewControl({
  width,
  height,
  frame,
  follow,
  panelOpen,
}: {
  width: number;
  height: number;
  frame: React.RefObject<HTMLDivElement | null>;
  follow: { x: number; y: number; at: number } | null;
  panelOpen: boolean;
}) {
  const { setViewport, getViewport } = useReactFlow();
  const opened = useRef(false);
  // Read when a step arrives. Closing the panel later shouldn't move the view.
  const panelIsOpen = useRef(panelOpen);
  useEffect(() => {
    panelIsOpen.current = panelOpen;
  }, [panelOpen]);

  useEffect(() => {
    const box = frame.current;
    if (opened.current || width === 0 || !box) return;
    opened.current = true;
    const fit = Math.min(
      (box.clientWidth - VIEW_MARGIN * 2) / width,
      (box.clientHeight - VIEW_MARGIN * 2) / height,
      1,
    );
    const zoom = Math.max(fit, MIN_READABLE_ZOOM);
    const fitsAcross = width * zoom <= box.clientWidth - VIEW_MARGIN * 2;
    void setViewport({
      x: fitsAcross ? (box.clientWidth - width * zoom) / 2 : VIEW_MARGIN,
      y: VIEW_MARGIN,
      zoom,
    });
  }, [width, height, frame, setViewport]);

  const followX = follow?.x;
  const followY = follow?.y;
  const followAt = follow?.at;
  useEffect(() => {
    const box = frame.current;
    if (followX === undefined || followY === undefined || followAt === undefined) return;
    if (!box || !opened.current) return;
    // Only a step that's arriving now moves the view. Picking another branch never does.
    if (timelineNow() > followAt + ENTRANCE_MS) return;
    const { x, y, zoom } = getViewport();
    // A new step must not land underneath the open sidebar, where its entrance would go unseen.
    const covered = panelIsOpen.current ? PANEL_WIDTH : 0;
    const visibleWidth = box.clientWidth > covered + BLOCK_WIDTH ? box.clientWidth - covered : box.clientWidth;
    const blockWidth = BLOCK_WIDTH * zoom;
    const blockHeight = (BLOCK_HEIGHT + TERMINUS_HEIGHT + TERMINUS_GAP) * zoom;
    const left = followX * zoom + x;
    const top = followY * zoom + y;
    const fitsAcross = left >= 0 && left + blockWidth <= visibleWidth;
    const fitsDown = top >= 0 && top + blockHeight <= box.clientHeight;
    if (fitsAcross && fitsDown) return;
    void setViewport(
      {
        x: fitsAcross ? x : (visibleWidth - blockWidth) / 2 - followX * zoom,
        y: fitsDown ? y : box.clientHeight - VIEW_MARGIN - blockHeight - followY * zoom,
        zoom,
      },
      { duration: 450 },
    );
  }, [followX, followY, followAt, frame, getViewport, setViewport]);

  return null;
}

/** Shows the whole tree. The tree's size is already known, so nothing needs measuring. */
function FitButton({
  width,
  height,
  frame,
}: {
  width: number;
  height: number;
  frame: React.RefObject<HTMLDivElement | null>;
}) {
  const { setViewport } = useReactFlow();
  const fit = () => {
    const box = frame.current;
    if (!box || width === 0) return;
    const zoom = Math.max(
      MIN_ZOOM,
      Math.min(
        (box.clientWidth - VIEW_MARGIN * 2) / width,
        (box.clientHeight - VIEW_MARGIN * 2) / height,
        1,
      ),
    );
    void setViewport(
      {
        x: (box.clientWidth - width * zoom) / 2,
        y: (box.clientHeight - height * zoom) / 2,
        zoom,
      },
      { duration: 300 },
    );
  };
  return (
    <ControlButton
      className="react-flow__controls-fitview"
      onClick={fit}
      title="Show the whole tree"
      aria-label="Show the whole tree"
    >
      <svg viewBox="0 0 32 30" aria-hidden="true">
        <path d="M3.7 11.2c.8 0 1.5-.7 1.5-1.5V5.2h4.4c.8 0 1.5-.7 1.5-1.5s-.7-1.5-1.5-1.5H3.7c-.8 0-1.5.7-1.5 1.5v6c0 .8.7 1.5 1.5 1.5zm24.6 0c.8 0 1.5-.7 1.5-1.5v-6c0-.8-.7-1.5-1.5-1.5h-5.9c-.8 0-1.5.7-1.5 1.5s.7 1.5 1.5 1.5h4.4v4.5c0 .8.7 1.5 1.5 1.5zM9.6 24.8H5.2v-4.5c0-.8-.7-1.5-1.5-1.5s-1.5.7-1.5 1.5v6c0 .8.7 1.5 1.5 1.5h5.9c.8 0 1.5-.7 1.5-1.5s-.7-1.5-1.5-1.5zm18.7-6c-.8 0-1.5.7-1.5 1.5v4.5h-4.4c-.8 0-1.5.7-1.5 1.5s.7 1.5 1.5 1.5h5.9c.8 0 1.5-.7 1.5-1.5v-6c0-.8-.7-1.5-1.5-1.5z" />
      </svg>
    </ControlButton>
  );
}

const MINIMAP_COLORS: Record<BlockInfo["kind"], string> = {
  task: "#f2efea",
  you: "#c4a7ff",
  model: "#62c6ff",
  call: "#3ddc97",
  result: "#f2d544",
  answer: "#ffffff",
};

function minimapColor(node: Node): string {
  const step = (node.data as Partial<BlockData>).node;
  return step ? MINIMAP_COLORS[blockInfo(step).kind] : "transparent";
}

interface Props {
  tree: Tree;
  branches: BranchInfo[];
  arrivals: Arrivals;
  selectedNodeId: string | null;
  selectedBranchId: string | null;
  highlighted: ReadonlySet<string>;
  onSelectNode: (id: string) => void;
  onSelectBranch: (id: string) => void;
  onClearSelection: () => void;
  panelOpen: boolean;
}

export function TrackMap({
  tree,
  branches,
  arrivals,
  selectedNodeId,
  selectedBranchId,
  highlighted,
  onSelectNode,
  onSelectBranch,
  onClearSelection,
  panelOpen,
}: Props) {
  const frame = useRef<HTMLDivElement>(null);

  const { nodes, edges, width, height, follow } = useMemo(() => {
    const stops = layoutTree(tree);
    const flowNodes: Node[] = [];
    const flowEdges: Edge[] = [];
    const position = (lane: number, depth: number) => ({ x: lane * LANE_X, y: depth * STEP_Y });
    let maxX = 0;
    let maxY = 0;
    let follow: { x: number; y: number; at: number } | null = null;
    for (const stop of stops.values()) {
      const { node } = stop;
      const place = position(stop.lane, stop.depth);
      maxX = Math.max(maxX, place.x + BLOCK_WIDTH);
      maxY = Math.max(maxY, place.y + BLOCK_HEIGHT + TERMINUS_GAP + TERMINUS_HEIGHT);
      const entranceAt = arrivals.get(node.id);
      if (entranceAt !== undefined && highlighted.has(node.id) && (!follow || entranceAt > follow.at)) {
        follow = { ...place, at: entranceAt };
      }
      flowNodes.push({
        id: node.id,
        type: "block",
        position: place,
        width: BLOCK_WIDTH,
        height: BLOCK_HEIGHT,
        handles: BLOCK_HANDLES,
        draggable: false,
        selectable: false,
        data: {
          node,
          selected: node.id === selectedNodeId,
          dim: !highlighted.has(node.id),
          entranceAt,
          onSelect: onSelectNode,
        },
      });
      if (node.parent_id) {
        const kind = node.edited_from_id
          ? "arrow-edit"
          : highlighted.has(node.id)
            ? "arrow-path"
            : "";
        flowEdges.push({
          id: `${node.parent_id}-${node.id}`,
          source: node.parent_id,
          target: node.id,
          type: "arrow",
          selectable: false,
          focusable: false,
          data: { kind, entranceAt },
        });
      }
    }
    for (const info of branches) {
      const stop = info.lastNode && stops.get(info.lastNode.id);
      if (!stop) continue;
      const place = position(stop.lane, stop.depth);
      flowNodes.push({
        id: `terminus-${info.branch.id}`,
        type: "terminus",
        position: { x: place.x, y: place.y + BLOCK_HEIGHT + TERMINUS_GAP },
        width: BLOCK_WIDTH,
        height: TERMINUS_HEIGHT,
        draggable: false,
        selectable: false,
        data: {
          info,
          selected: info.branch.id === selectedBranchId,
          onSelect: onSelectBranch,
        },
      });
    }
    return { nodes: flowNodes, edges: flowEdges, width: maxX, height: maxY, follow };
  }, [tree, branches, arrivals, selectedNodeId, selectedBranchId, highlighted, onSelectNode, onSelectBranch]);

  return (
    <div className="map" ref={frame}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        minZoom={MIN_ZOOM}
        maxZoom={1.5}
        onPaneClick={onClearSelection}
      >
        <ViewControl width={width} height={height} frame={frame} follow={follow} panelOpen={panelOpen} />
        <Background variant={BackgroundVariant.Dots} gap={LANE_X / 4} size={1} color="#262626" />
        <Controls showInteractive={false} showFitView={false}>
          <FitButton width={width} height={height} frame={frame} />
        </Controls>
        <MiniMap
          pannable
          zoomable
          nodeColor={minimapColor}
          bgColor="#141414"
          maskColor="rgb(0 0 0 / 0.65)"
          maskStrokeColor="#ff7a00"
          ariaLabel="Overview of the whole tree"
        />
      </ReactFlow>
    </div>
  );
}
