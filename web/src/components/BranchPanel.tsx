"use client";

import { ContinueForm } from "./ContinueForm";
import { ACTIVE_STATUSES, type BranchInfo, STATUS_LABELS, canContinue } from "@/lib/nodes";

interface Props {
  open: boolean;
  branches: BranchInfo[];
  selectedBranchId: string | null;
  comparing: readonly string[];
  error: string | null;
  onClose: () => void;
  onSelect: (id: string) => void;
  onToggleCompare: (id: string) => void;
  onCompare: () => void;
  onCancel: (id: string) => void;
  turnLimit: number | null;
  onContinued: (id: string) => void;
}

export function BranchPanel({
  open,
  branches,
  selectedBranchId,
  comparing,
  error,
  onClose,
  onSelect,
  onToggleCompare,
  onCompare,
  onCancel,
  turnLimit,
  onContinued,
}: Props) {
  return (
    <aside
      className={`sidebar sidebar-left ${open ? "sidebar-open" : ""}`}
      aria-label="Branches"
      inert={!open}
    >
      <div className="sidebar-head">
        <h2>Branches</h2>
        <button className="button" type="button" onClick={onClose}>
          Close branches
        </button>
      </div>
      <div className="compare-bar">
        <button
          className="button button-primary"
          type="button"
          disabled={comparing.length !== 2}
          onClick={onCompare}
        >
          Compare branches
        </button>
        <span className="quiet small">
          {comparing.length === 2
            ? "Two branches picked."
            : `Pick ${2 - comparing.length} more to compare.`}
        </span>
      </div>
      {error && (
        <p className="error small" role="alert">
          {error}
        </p>
      )}
      <ul className="branch-list">
        {branches.map(({ branch, name, change }) => {
          const picked = comparing.includes(branch.id);
          return (
            <li
              key={branch.id}
              className={`branch-row ${branch.id === selectedBranchId ? "branch-row-selected" : ""}`}
            >
              <input
                type="checkbox"
                aria-label={`Compare ${name}`}
                checked={picked}
                disabled={!picked && comparing.length === 2}
                onChange={() => onToggleCompare(branch.id)}
              />
              <div className="branch-row-main">
                <button className="branch-name" type="button" onClick={() => onSelect(branch.id)}>
                  <i className={`lamp lamp-${branch.status}`} aria-hidden="true" />
                  {name}
                </button>
                <p className="small quiet">{change}</p>
                <p className="small" title={branch.status_reason ?? undefined}>
                  {STATUS_LABELS[branch.status]}. {branch.turns_used}{" "}
                  {branch.turns_used === 1 ? "turn" : "turns"},{" "}
                  {(branch.input_tokens + branch.output_tokens).toLocaleString()} tokens.
                </p>
                {canContinue(branch) && (
                  <ContinueForm
                    collapsed
                    branch={branch}
                    turnLimit={turnLimit === null ? null : turnLimit + branch.extra_turns}
                    onContinued={onContinued}
                  />
                )}
                {ACTIVE_STATUSES.has(branch.status) && (
                  <button className="link-button small" type="button" onClick={() => onCancel(branch.id)}>
                    Cancel {name}
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
