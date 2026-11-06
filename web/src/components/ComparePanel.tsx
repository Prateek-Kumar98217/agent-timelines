"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { diffWords } from "@/lib/diff";
import { type BranchInfo, STATUS_LABELS } from "@/lib/nodes";
import type { BranchSummary, Comparison } from "@/lib/types";

function verdict(comparison: Comparison): string {
  if (comparison.same_final_text) return "Both branches gave exactly the same answer.";
  if (comparison.final_similarity === null) {
    return "At least one branch has no final answer yet, so the answers can't be compared.";
  }
  const score = comparison.final_similarity.toFixed(3);
  return comparison.converged
    ? `The answers read alike, with a similarity of ${score}. That doesn't mean they agree: a changed date or figure barely moves this score, so check the marked words.`
    : `The answers differ in meaning, with a similarity of ${score}.`;
}

function Answer({ parts, text, mark }: { parts: { text: string; changed: boolean }[] | null; text: string | null; mark: string }) {
  if (text === null) return <p className="compare-answer quiet">This branch has no final answer.</p>;
  return (
    <div className="compare-answer prose">
      {parts
        ? parts.map((part, index) =>
            part.changed && part.text.trim() ? (
              <mark key={index} className={mark} style={{ color: "inherit" }}>
                {part.text}
              </mark>
            ) : (
              <span key={index}>{part.text}</span>
            ),
          )
        : text}
    </div>
  );
}

function Tools({ summary }: { summary: BranchSummary }) {
  if (summary.tool_calls.length === 0) return <>None</>;
  return (
    <ol className="mono">
      {summary.tool_calls.map((call, index) => (
        <li key={index}>{call}</li>
      ))}
    </ol>
  );
}

interface Props {
  a: BranchInfo;
  b: BranchInfo;
  onClose: () => void;
}

export function ComparePanel({ a, b, onClose }: Props) {
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const finishedKey = `${a.branch.status}/${b.branch.status}/${a.branch.turns_used}/${b.branch.turns_used}`;

  useEffect(() => {
    let current = true;
    api
      .compare(a.branch.id, b.branch.id)
      .then((result) => {
        if (current) setComparison(result);
      })
      .catch((caught: Error) => {
        if (current) setError(caught.message);
      });
    return () => {
      current = false;
    };
  }, [a.branch.id, b.branch.id, finishedKey]);

  const diff = useMemo(() => {
    const left = comparison?.a.final_text;
    const right = comparison?.b.final_text;
    return left != null && right != null ? diffWords(left, right) : null;
  }, [comparison]);

  const scoreNames = comparison
    ? [...new Set([...Object.keys(comparison.a.scores), ...Object.keys(comparison.b.scores)])].sort()
    : [];

  return (
    <section className="compare" aria-label="Compare branches">
      <div className="compare-inner">
        <p style={{ marginBottom: "1rem" }}>
          <button className="button" type="button" onClick={onClose}>
            Back to the map
          </button>
        </p>
        <h2>
          {a.name} and {b.name}
        </h2>
        {error && <p className="error" role="alert">{error}</p>}
        {!comparison && !error && <p className="quiet">Comparing the answers.</p>}
        {comparison && (
          <>
            <p className="compare-verdict">{verdict(comparison)}</p>
            <div className="compare-sides">
              {([[a, comparison.a, diff?.a ?? null, "diff-a"], [b, comparison.b, diff?.b ?? null, "diff-b"]] as const).map(
                ([info, summary, parts, mark]) => (
                  <div className="compare-side" key={info.branch.id}>
                    <h3>
                      <i className={`lamp lamp-${summary.status}`} aria-hidden="true" />
                      {info.name}
                    </h3>
                    <p className="quiet small">{info.change}</p>
                    <Answer parts={parts} text={summary.final_text} mark={mark} />
                  </div>
                ),
              )}
            </div>
            <table className="compare-table">
              <thead>
                <tr>
                  <th scope="col">Measure</th>
                  <th scope="col">{a.name}</th>
                  <th scope="col">{b.name}</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row">Status</th>
                  <td>{STATUS_LABELS[comparison.a.status]}</td>
                  <td>{STATUS_LABELS[comparison.b.status]}</td>
                </tr>
                <tr>
                  <th scope="row">Turns</th>
                  <td>{comparison.a.turns_used}</td>
                  <td>{comparison.b.turns_used}</td>
                </tr>
                <tr>
                  <th scope="row">Tokens</th>
                  <td>{(comparison.a.input_tokens + comparison.a.output_tokens).toLocaleString()}</td>
                  <td>{(comparison.b.input_tokens + comparison.b.output_tokens).toLocaleString()}</td>
                </tr>
                <tr>
                  <th scope="row">Tools used</th>
                  <td><Tools summary={comparison.a} /></td>
                  <td><Tools summary={comparison.b} /></td>
                </tr>
                {scoreNames.map((name) => (
                  <tr key={name}>
                    <th scope="row">Score: {name.replaceAll("_", " ")}</th>
                    <td>{comparison.a.scores[name]?.toFixed(2) ?? "Not scored"}</td>
                    <td>{comparison.b.scores[name]?.toFixed(2) ?? "Not scored"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="quiet small" style={{ marginTop: "0.75rem" }}>
              They share {comparison.shared_nodes} steps before splitting.{" "}
              {Math.round(comparison.tool_call_overlap * 100)}% of their tool calls after the split match.
            </p>
          </>
        )}
      </div>
    </section>
  );
}
