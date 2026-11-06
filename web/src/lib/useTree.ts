"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { ENTRANCE_MS, timelineNow } from "./entrance";
import { withoutTaskCopy } from "./nodes";
import type { Tree } from "./types";

const EVENT_TYPES = ["branch_created", "branch_status", "node_created"];
const REFRESH_DELAY_MS = 200;
const REOPEN_DELAY_MS = 2000;
/** New steps play one after another, so a burst still reads as a sequence. */
const ENTRANCE_GAP_MS = ENTRANCE_MS - 200;
/** Gives React time to put a new block on the page before its entrance begins. */
const ENTRANCE_LEAD_MS = 60;

export type Connection = "connecting" | "live" | "reconnecting";

/** Which steps arrived after the page opened, with the moment each one's entrance starts. */
export type Arrivals = ReadonlyMap<string, number>;

const NO_ARRIVALS: Arrivals = new Map();

/**
 * The tree, kept current from its event stream. Events only say that something changed, and the
 * tree is read again, so a missed event can't leave the page wrong.
 */
export function useTree(id: string) {
  const [tree, setTree] = useState<Tree | null>(null);
  const [arrivals, setArrivals] = useState<Arrivals>(NO_ARRIVALS);
  const [error, setError] = useState<Error | null>(null);
  const [connection, setConnection] = useState<Connection>("connecting");
  const newest = useRef(-1);
  const known = useRef<Set<string> | null>(null);
  const nextEntrance = useRef(0);

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const next = withoutTaskCopy(await api.tree(id, signal));
        // A slower, older response must not replace a newer one.
        if (next.last_event_seq < newest.current) return;
        newest.current = next.last_event_seq;
        const seen = known.current;
        known.current = new Set(next.nodes.map((node) => node.id));
        // Everything on the first load was already there, so only later steps get an entrance.
        const added = seen ? next.nodes.filter((node) => !seen.has(node.id)) : [];
        if (added.length > 0) {
          const starts = added.map((node) => {
            const startsAt = Math.max(timelineNow() + ENTRANCE_LEAD_MS, nextEntrance.current);
            nextEntrance.current = startsAt + ENTRANCE_GAP_MS;
            return [node.id, startsAt] as const;
          });
          setArrivals((current) => new Map([...current, ...starts]));
        }
        setTree(next);
        setError(null);
      } catch (caught) {
        if ((caught as Error).name !== "AbortError") setError(caught as Error);
      }
    },
    [id],
  );

  useEffect(() => {
    const controller = new AbortController();
    newest.current = -1;
    known.current = null;

    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {
      if (timer === undefined) {
        timer = setTimeout(() => {
          timer = undefined;
          void refresh(controller.signal);
        }, REFRESH_DELAY_MS);
      }
    };
    schedule();

    let source: EventSource;
    let reopen: ReturnType<typeof setTimeout> | undefined;
    const connect = () => {
      source = new EventSource(`/api/trees/${id}/stream`);
      source.onopen = () => {
        setConnection("live");
        schedule();
      };
      source.onerror = () => {
        setConnection("reconnecting");
        // A browser retries a dropped stream by itself, but gives up after an HTTP error.
        if (source.readyState === EventSource.CLOSED) reopen = setTimeout(connect, REOPEN_DELAY_MS);
      };
      for (const type of EVENT_TYPES) source.addEventListener(type, schedule);
    };
    connect();

    return () => {
      controller.abort();
      clearTimeout(timer);
      clearTimeout(reopen);
      source.close();
    };
  }, [id, refresh]);

  return { tree, arrivals, error, connection, refresh };
}
