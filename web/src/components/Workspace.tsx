"use client";

import { ReactFlowProvider } from "@xyflow/react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import {
  ACTIVE_STATUSES,
  type BranchInfo,
  canContinue,
  changedTheTask,
  describeBranches,
  pathTo,
  taskOfTree,
  toolsOf,
  turnLimitOf,
} from "@/lib/nodes";
import { useTree } from "@/lib/useTree";
import { BranchPanel } from "./BranchPanel";
import { ComparePanel } from "./ComparePanel";
import { Inspector } from "./Inspector";
import { LimitsBar } from "./LimitsBar";
import { TrackMap } from "./TrackMap";

export function Workspace({ id }: { id: string }) {
  const { tree, arrivals, error, connection, refresh } = useTree(id);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedBranchId, setSelectedBranchId] = useState<string | null>(null);
  const [comparing, setComparing] = useState<string[]>([]);
  const [showCompare, setShowCompare] = useState(false);
  const [showBranches, setShowBranches] = useState(false);
  const [showStep, setShowStep] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const branches = useMemo(() => (tree ? describeBranches(tree) : []), [tree]);
  const tools = useMemo(() => (tree ? toolsOf(tree) : []), [tree]);

  // Until something is picked, follow the original run.
  const branchId = selectedBranchId ?? branches[0]?.branch.id ?? null;
  const info = branches.find((entry) => entry.branch.id === branchId) ?? null;
  const path = useMemo(
    () => (tree && info?.lastNode ? pathTo(tree, info.lastNode.id) : []),
    [tree, info],
  );
  const highlighted = useMemo(() => new Set(path.map((node) => node.id)), [path]);
  // A branch opens on the step that was edited to create it. The original run has none, so when
  // it's finished it opens on its last step, which is where it can continue.
  const node =
    tree?.nodes.find((entry) => entry.id === selectedNodeId) ??
    (selectedNodeId === null && selectedBranchId
      ? (tree?.nodes.find(
          (entry) => entry.branch_id === selectedBranchId && entry.edited_from_id,
        ) ?? (info && canContinue(info.branch) ? info.lastNode : undefined))
      : undefined) ??
    null;

  const selectBranch = useCallback((nextId: string) => {
    setSelectedBranchId(nextId);
    setSelectedNodeId(null);
    setShowStep(true);
  }, []);

  const clearSelection = useCallback(() => setShowStep(false), []);

  // A step on a dimmed line belongs to another branch, so picking it switches branch too.
  const selectNode = useCallback(
    (nextId: string) => {
      setSelectedNodeId(nextId);
      setShowStep(true);
      if (!tree) return;
      const passesThrough = (entry: BranchInfo) =>
        entry.lastNode !== null && pathTo(tree, entry.lastNode.id).some((step) => step.id === nextId);
      const all = describeBranches(tree);
      setSelectedBranchId((current) => {
        const stays = all.some((entry) => entry.branch.id === current && passesThrough(entry));
        return stays ? current : (all.find(passesThrough)?.branch.id ?? current);
      });
    },
    [tree],
  );

  const forked = useCallback(
    (branch: string) => {
      selectBranch(branch);
      void refresh();
    },
    [refresh, selectBranch],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setShowCompare(false);
      setShowStep(false);
      setShowBranches(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (error && !tree) {
    return (
      <main className="centered-state">
        <h1>This task can't be opened</h1>
        <p role="alert">{error.message}</p>
        <Link className="button" href="/">
          Back to your tasks
        </Link>
      </main>
    );
  }
  if (!tree) return <main className="centered-state quiet">Loading the task.</main>;

  const baseTurnLimit = turnLimitOf(tree);
  const selectedTurnLimit =
    baseTurnLimit === null ? null : baseTurnLimit + (info?.branch.extra_turns ?? 0);
  const running = branches.filter((entry) => ACTIVE_STATUSES.has(entry.branch.status)).length;
  const compareA = branches.find((entry) => entry.branch.id === comparing[0]);
  const compareB = branches.find((entry) => entry.branch.id === comparing[1]);

  return (
    <main className="workspace">
      <header className="topbar">
        <Link className="wordmark" href="/">
          agent-timelines
        </Link>
        <h1 className="topbar-task" style={{ fontSize: "inherit" }} title={taskOfTree(tree)}>
          {taskOfTree(tree)}
        </h1>
        <span className="connection quiet small" role="status">
          {connection === "reconnecting"
            ? "Updates paused, reconnecting"
            : running === 0
              ? "All branches finished"
              : running === 1
                ? "1 branch running"
                : `${running} branches running`}
        </span>
        <button
          className="button"
          type="button"
          aria-expanded={showBranches}
          onClick={() => setShowBranches(!showBranches)}
        >
          Branches ({branches.length})
        </button>
      </header>
      <LimitsBar
        refreshKey={tree.last_event_seq}
        turnLimit={selectedTurnLimit}
        turnsUsed={info?.branch.turns_used ?? 0}
        branchName={info?.name ?? ""}
      />
      <div className="stage">
      <ReactFlowProvider>
        <TrackMap
          tree={tree}
          branches={branches}
          arrivals={arrivals}
          selectedNodeId={node?.id ?? null}
          selectedBranchId={branchId}
          highlighted={highlighted}
          onSelectNode={selectNode}
          onSelectBranch={selectBranch}
          onClearSelection={clearSelection}
          panelOpen={showStep && node !== null}
        />
      </ReactFlowProvider>
      <BranchPanel
        open={showBranches}
        branches={branches}
        selectedBranchId={branchId}
        comparing={comparing}
        error={actionError}
        onClose={() => setShowBranches(false)}
        onSelect={selectBranch}
        onToggleCompare={(toggled) =>
          setComparing((current) =>
            current.includes(toggled)
              ? current.filter((entry) => entry !== toggled)
              : [...current, toggled],
          )
        }
        onCompare={() => {
          setShowBranches(false);
          setShowCompare(true);
        }}
        turnLimit={turnLimitOf(tree)}
        onContinued={forked}
        onCancel={(cancelled) => {
          setActionError(null);
          api
            .cancel(cancelled)
            .then(() => refresh())
            .catch((caught: Error) => setActionError(caught.message));
        }}
      />
      <Inspector
        turnLimit={selectedTurnLimit}
        taskChanged={node !== null && changedTheTask(tree, node)}
        canContinue={
          info !== null && canContinue(info.branch) && node?.id === info.lastNode?.id
        }
        onContinued={forked}
        open={showStep}
        node={node}
        branch={info}
        path={path}
        tools={tools}
        onClose={() => setShowStep(false)}
        onSelect={selectNode}
        onForked={forked}
      />
      <aside
        className={`sidebar sidebar-wide ${showCompare && compareA && compareB ? "sidebar-open" : ""}`}
        aria-label="Comparison"
        inert={!(showCompare && compareA && compareB)}
      >
        {showCompare && compareA && compareB && (
          <ComparePanel a={compareA} b={compareB} onClose={() => setShowCompare(false)} />
        )}
      </aside>
      </div>
    </main>
  );
}
