"use client";

import { ReactFlowProvider } from "@xyflow/react";
import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { ACTIVE_STATUSES, type BranchInfo, describeBranches, pathTo, taskOfTree, toolsOf } from "@/lib/nodes";
import { useTree } from "@/lib/useTree";
import { BranchTable } from "./BranchTable";
import { ComparePanel } from "./ComparePanel";
import { Inspector } from "./Inspector";
import { TrackMap } from "./TrackMap";

export function Workspace({ id }: { id: string }) {
  const { tree, error, connection, refresh } = useTree(id);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedBranchId, setSelectedBranchId] = useState<string | null>(null);
  const [comparing, setComparing] = useState<string[]>([]);
  const [showCompare, setShowCompare] = useState(false);
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
  // A branch opens on the step that was edited to create it.
  const node =
    tree?.nodes.find((entry) => entry.id === selectedNodeId) ??
    (selectedNodeId === null && selectedBranchId
      ? tree?.nodes.find((entry) => entry.branch_id === selectedBranchId && entry.edited_from_id)
      : undefined) ??
    null;

  const selectBranch = useCallback((nextId: string) => {
    setSelectedBranchId(nextId);
    setSelectedNodeId(null);
  }, []);

  // A step on a dimmed line belongs to another branch, so picking it switches branch too.
  const selectNode = useCallback(
    (nextId: string) => {
      setSelectedNodeId(nextId);
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
      </header>
      {showCompare && compareA && compareB ? (
        <ComparePanel a={compareA} b={compareB} onClose={() => setShowCompare(false)} />
      ) : (
        <>
          <ReactFlowProvider>
            <TrackMap
              tree={tree}
              branches={branches}
              selectedNodeId={selectedNodeId}
              selectedBranchId={branchId}
              highlighted={highlighted}
              onSelectNode={selectNode}
              onSelectBranch={selectBranch}
            />
          </ReactFlowProvider>
          <Inspector
            node={node}
            branch={info}
            path={path}
            tools={tools}
            onSelect={selectNode}
            onForked={forked}
          />
          <div style={{ display: "contents" }}>
            <BranchTable
              branches={branches}
              selectedBranchId={branchId}
              comparing={comparing}
              onSelect={selectBranch}
              onToggleCompare={(toggled) =>
                setComparing((current) =>
                  current.includes(toggled)
                    ? current.filter((entry) => entry !== toggled)
                    : [...current, toggled],
                )
              }
              error={actionError}
              onCompare={() => setShowCompare(true)}
              onCancel={(cancelled) => {
                setActionError(null);
                api
                  .cancel(cancelled)
                  .then(() => refresh())
                  .catch((caught: Error) => setActionError(caught.message));
              }}
            />
          </div>
        </>
      )}
    </main>
  );
}
