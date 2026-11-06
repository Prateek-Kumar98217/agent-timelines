"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { TreeSummary } from "@/lib/types";

const PAGE = 25;
const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export function TreeList() {
  const [trees, setTrees] = useState<TreeSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState(PAGE);

  useEffect(() => {
    api
      .trees()
      .then(setTrees)
      .catch((caught: Error) => setError(caught.message));
  }, []);

  const matching = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle
      ? (trees ?? []).filter((tree) => tree.task.toLowerCase().includes(needle))
      : (trees ?? []);
  }, [trees, query]);

  if (error) return <p className="error tree-section">{error}</p>;
  if (trees === null) return null;
  if (trees.length === 0) {
    return (
      <p className="tree-section quiet">Your tasks will appear here once you've started one.</p>
    );
  }

  return (
    <section className="tree-section">
      <div className="tree-section-head">
        <h2>Your tasks</h2>
        <label className="tree-search">
          <span className="field-label small">Search {trees.length.toLocaleString()} tasks</span>
          <input
            className="field"
            type="search"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setShown(PAGE);
            }}
          />
        </label>
      </div>
      <ul className="tree-list">
        {matching.slice(0, shown).map((tree) => (
          <li key={tree.id}>
            <Link className="tree-row" href={`/tree?id=${tree.id}`}>
              <span className="tree-task">{tree.task}</span>
              <span className="tree-meta small quiet">
                {tree.branches === 1 ? "1 branch" : `${tree.branches} branches`}
                <br />
                {dateFormat.format(new Date(tree.created_at))}
              </span>
            </Link>
          </li>
        ))}
      </ul>
      {matching.length === 0 && <p className="quiet">No tasks match that search.</p>}
      {matching.length > shown && (
        <p style={{ marginTop: "1rem" }}>
          <button className="button" type="button" onClick={() => setShown(shown + PAGE)}>
            Show more tasks
          </button>
        </p>
      )}
    </section>
  );
}
