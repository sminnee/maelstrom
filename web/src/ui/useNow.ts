import { useSyncExternalStore } from 'react';

/**
 * One clock for every age on screen.
 *
 * An age has to tick without a message arriving, or "<1m" freezes and lies in
 * exactly the way this display exists to stop. One shared interval does that
 * for a desk of forty cards; one timer per card would not be worth the same
 * label.
 */

/**
 * How often a displayed age is refreshed. Under the minute an age displays,
 * so no label is ever visibly stale, and cheap enough that a desk of forty
 * cards costs nothing.
 */
const TICK_MS = 30_000;

let now = Date.now();
let timer: ReturnType<typeof setInterval> | null = null;
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (timer === null) {
    // The stored time is as old as the last tick, which on a page that showed
    // no age at all is module load. Read the clock as the first age arrives,
    // so it opens correct rather than correcting itself 30s later.
    now = Date.now();
    timer = setInterval(() => {
      now = Date.now();
      for (const l of listeners) l();
    }, TICK_MS);
  }
  return () => {
    listeners.delete(listener);
    // The last age left the screen: stop the timer rather than wake for nobody.
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

function getSnapshot(): number {
  return now;
}

/** The current time, re-rendering the caller on every tick. */
export function useNow(): number {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
