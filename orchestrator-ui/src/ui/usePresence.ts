import { useLayoutEffect, useState, type RefObject } from 'react';

/** How long past its transition a leaving surface waits for `transitionend`, which may not come. */
const SLACK_MS = 50;

/**
 * Keeps a surface in the page while its CSS exit transition plays. The surface
 * draws `data-presence="out"` while it leaves, and its CSS module transitions to
 * that state; it enters from `@starting-style`. `show` true again mid-exit turns
 * the transition back. Returns whether to render, and whether the surface is leaving.
 *
 * The surface unmounts on its own `transitionend`, or a little after the
 * transition's length if none ends. Where no transition runs (reduced motion, a
 * test) it unmounts at once.
 */
export function usePresence(
  show: boolean,
  ref: RefObject<HTMLElement | null>,
): { mounted: boolean; leaving: boolean } {
  const [mounted, setMounted] = useState(show);
  if (show && !mounted) setMounted(true);

  useLayoutEffect(() => {
    if (show || !mounted) return;
    const el = ref.current;
    const ms = el ? transitionMs(el) : 0;
    if (!el || ms === 0) {
      setMounted(false);
      return;
    }
    const done = () => setMounted(false);
    // Only the surface's own transition: a child's hover fade bubbles here too.
    const onEnd = (e: TransitionEvent) => {
      if (e.target === el) done();
    };
    el.addEventListener('transitionend', onEnd);
    const timer = setTimeout(done, ms + SLACK_MS);
    return () => {
      el.removeEventListener('transitionend', onEnd);
      clearTimeout(timer);
    };
  }, [show, mounted, ref]);

  return { mounted, leaving: mounted && !show };
}

/** The longest of an element's transitions, delay included, in ms. */
function transitionMs(el: HTMLElement): number {
  const style = getComputedStyle(el);
  const durations = times(style.transitionDuration);
  const delays = times(style.transitionDelay);
  return Math.max(0, ...durations.map((d, i) => d + (delays[i] ?? 0)));
}

/** `"0.18s, 120ms"` as `[180, 120]`. An empty or unknown value is 0. */
const times = (list: string): number[] =>
  list.split(',').map((t) => {
    const n = parseFloat(t);
    if (!Number.isFinite(n)) return 0;
    return t.trim().endsWith('ms') ? n : n * 1000;
  });
