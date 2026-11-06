"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Limits } from "@/lib/types";

const ROLE_NAMES: Record<string, string> = {
  local: "Local user",
  user: "Signed in",
  guest: "Guest",
};
// Branches on other tasks can start or finish while this page is open, with no event to say so.
const REFRESH_MS = 10_000;
const MOST_PIPS = 30;

function Pips({ used, limit }: { used: number; limit: number }) {
  if (limit > MOST_PIPS) return null;
  return (
    <span className="pips" aria-hidden="true">
      {Array.from({ length: limit }, (_, index) => (
        <i key={index} className={`pip ${index < used ? "pip-on" : ""}`} />
      ))}
    </span>
  );
}

interface Props {
  /** Reloads the counts whenever this changes. */
  refreshKey?: number;
  turnLimit?: number | null;
  turnsUsed?: number;
  branchName?: string;
}

export function LimitsBar({ refreshKey, turnLimit, turnsUsed, branchName }: Props) {
  const [limits, setLimits] = useState<Limits | null>(null);

  useEffect(() => {
    let current = true;
    const load = () => {
      api
        .limits()
        .then((next) => {
          if (current) setLimits(next);
        })
        // This bar is only information, and the page reports a lost connection elsewhere.
        .catch(() => undefined);
    };
    const timer = setTimeout(load, 0);
    const interval = setInterval(load, REFRESH_MS);
    return () => {
      current = false;
      clearTimeout(timer);
      clearInterval(interval);
    };
  }, [refreshKey]);

  if (!limits) return null;
  const allowedTurns = turnLimit ?? limits.max_turns;
  const full = limits.active_branches >= limits.max_active_branches;

  return (
    <section className="limits" aria-label="Your limits">
      <span className="limits-role">{ROLE_NAMES[limits.role] ?? limits.role}</span>
      <span className={`limit ${full ? "limit-full" : ""}`}>
        <span>Active branches</span>
        <Pips used={limits.active_branches} limit={limits.max_active_branches} />
        <b>
          {limits.active_branches} of {limits.max_active_branches}
        </b>
      </span>
      {turnsUsed === undefined ? (
        <span className="limit">
          <span>Up to {allowedTurns} turns per path</span>
        </span>
      ) : (
        <span className={`limit ${turnsUsed >= allowedTurns ? "limit-full" : ""}`}>
          <span>{branchName}</span>
          <Pips used={turnsUsed} limit={allowedTurns} />
          <b>
            {turnsUsed} of {allowedTurns} turns
          </b>
        </span>
      )}
    </section>
  );
}
