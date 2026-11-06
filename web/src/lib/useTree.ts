"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Tree } from "./types";

const EVENT_TYPES = ["branch_created", "branch_status", "node_created"];
const REFRESH_DELAY_MS = 200;

export type Connection = "connecting" | "live" | "reconnecting";

/**
 * The tree, kept current from its event stream. Events only say that something changed, and the
 * tree is read again, so a missed event can't leave the page wrong.
 */
export function useTree(id: string) {
  const [tree, setTree] = useState<Tree | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [connection, setConnection] = useState<Connection>("connecting");
  const newest = useRef(-1);

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const next = await api.tree(id, signal);
        // A slower, older response must not replace a newer one.
        if (next.last_event_seq >= newest.current) {
          newest.current = next.last_event_seq;
          setTree(next);
          setError(null);
        }
      } catch (caught) {
        if ((caught as Error).name !== "AbortError") setError(caught as Error);
      }
    },
    [id],
  );

  useEffect(() => {
    const controller = new AbortController();
    newest.current = -1;

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

    const source = new EventSource(`/api/trees/${id}/stream`);
    source.onopen = () => {
      setConnection("live");
      schedule();
    };
    source.onerror = () => setConnection("reconnecting");
    for (const type of EVENT_TYPES) source.addEventListener(type, schedule);

    return () => {
      controller.abort();
      clearTimeout(timer);
      source.close();
    };
  }, [id, refresh]);

  return { tree, error, connection, refresh };
}
