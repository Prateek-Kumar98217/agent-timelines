"use client";

import { ACTIVE_STATUSES, type BranchInfo, STATUS_LABELS } from "@/lib/nodes";

interface Props {
  branches: BranchInfo[];
  selectedBranchId: string | null;
  comparing: readonly string[];
  error: string | null;
  onSelect: (id: string) => void;
  onToggleCompare: (id: string) => void;
  onCompare: () => void;
  onCancel: (id: string) => void;
}

export function BranchTable({
  branches,
  selectedBranchId,
  comparing,
  error,
  onSelect,
  onToggleCompare,
  onCompare,
  onCancel,
}: Props) {
  return (
    <section className="branches" aria-label="Branches">
      <table>
        <thead>
          <tr>
            <th scope="col">Compare</th>
            <th scope="col">Branch</th>
            <th scope="col">What changed</th>
            <th scope="col">Status</th>
            <th scope="col" className="numeric">Turns</th>
            <th scope="col" className="numeric">Tokens</th>
            <th scope="col">Action</th>
          </tr>
        </thead>
        <tbody>
          {branches.map(({ branch, name, change }) => {
            const picked = comparing.includes(branch.id);
            const active = ACTIVE_STATUSES.has(branch.status);
            return (
              <tr
                key={branch.id}
                className={branch.id === selectedBranchId ? "branch-row-selected" : undefined}
              >
                <td>
                  <input
                    type="checkbox"
                    aria-label={`Compare ${name}`}
                    checked={picked}
                    disabled={!picked && comparing.length === 2}
                    onChange={() => onToggleCompare(branch.id)}
                  />
                </td>
                <td>
                  <button className="branch-name" type="button" onClick={() => onSelect(branch.id)}>
                    <i className={`lamp lamp-${branch.status}`} aria-hidden="true" />
                    {name}
                  </button>
                </td>
                <td>{change}</td>
                <td title={branch.status_reason ?? undefined}>{STATUS_LABELS[branch.status]}</td>
                <td className="numeric">{branch.turns_used}</td>
                <td className="numeric">
                  {(branch.input_tokens + branch.output_tokens).toLocaleString()}
                </td>
                <td>
                  {active && (
                    <button className="link-button" type="button" onClick={() => onCancel(branch.id)}>
                      Cancel {name}
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="branches-foot">
        <button
          className="button"
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
        {error && (
          <span className="error small" role="alert">
            {error}
          </span>
        )}
      </div>
    </section>
  );
}
