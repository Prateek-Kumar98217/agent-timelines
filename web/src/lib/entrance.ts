"use client";

import { type RefObject, useLayoutEffect } from "react";

/** The order of one step's entrance: the block pops up, the arrow draws, and the head lands. */
export const POP_MS = 320;
export const DRAW_MS = 380;
export const HEAD_MS = 90;

interface Entrance {
  /** Shows up in `document.getAnimations()`, and lets tests tell the parts apart. */
  id: string;
  delay: number;
  duration: number;
  easing: string;
  keyframes: Keyframe[];
}

export const BLOCK_POP: Entrance = {
  id: "block-pop",
  delay: 0,
  duration: POP_MS,
  easing: "cubic-bezier(0.2, 1.5, 0.4, 1)",
  keyframes: [{ opacity: 0, transform: "scale(0.55)" }, { opacity: 1, transform: "none" }],
};

export const ARROW_DRAW: Entrance = {
  id: "arrow-draw",
  delay: POP_MS,
  duration: DRAW_MS,
  easing: "ease-in",
  keyframes: [{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }],
};

export const ARROW_HEAD: Entrance = {
  id: "arrow-head",
  delay: POP_MS + DRAW_MS,
  duration: HEAD_MS,
  easing: "linear",
  keyframes: [{ opacity: 0 }, { opacity: 1 }],
};

export const ENTRANCE_MS = POP_MS + DRAW_MS + HEAD_MS;

export function timelineNow(): number {
  return Number(document.timeline.currentTime ?? 0);
}

/** When each entrance actually began, so a rebuilt element can't begin it a second time. */
const begun = new Map<string, number>();

/**
 * Plays an entrance once, at a fixed moment on the page's clock. React Flow rebuilds its arrow
 * elements whenever its data changes, which would restart a CSS animation. Here a rebuilt element
 * carries on from the same point, and one built after the entrance ended plays nothing. A busy page
 * can put an element up late, so the first one to appear sets the moment if the planned one has
 * passed, and the entrance still plays.
 */
export function useEntrance(
  element: RefObject<Element | null>,
  owner: string,
  plannedAt: number | undefined,
  entrance: Entrance,
): void {
  useLayoutEffect(() => {
    const target = element.current;
    if (!target || plannedAt === undefined) return;
    const key = `${entrance.id}:${owner}`;
    const startsAt = begun.get(key) ?? Math.max(plannedAt, timelineNow() - entrance.delay);
    begun.set(key, startsAt);
    if (timelineNow() >= startsAt + entrance.delay + entrance.duration) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const animation = target.animate(entrance.keyframes, {
      id: entrance.id,
      delay: entrance.delay,
      duration: entrance.duration,
      easing: entrance.easing,
      fill: "backwards",
    });
    animation.startTime = startsAt;
    return () => animation.cancel();
  }, [element, owner, plannedAt, entrance]);
}
