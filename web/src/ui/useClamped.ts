import { useEffect, useState, type RefObject } from 'react';

/**
 * Whether a clamped element is actually cutting anything off.
 *
 * The clamp is a rendered height, so only the rendered content can answer.
 * Counting source lines misses a long line that wraps, and offers a control on
 * short content that already fits — and a control that reveals nothing is worse
 * than no control.
 *
 * Pass the deps that change the content or the clamp, the way you would to
 * `useEffect`: the measurement re-runs for each.
 *
 * `useEffect` rather than `useLayoutEffect`, though the measurement is a
 * layout read. Measuring before paint would save one frame without the
 * control, which no reader sees. Measuring synchronously on every render of
 * every card and decision costs ticks the transcript socket needs to open,
 * snapshot and reduce — and losing that race is visible. See
 * `docs/dev/orchestrator-ui.md`, "Transcripts are sockets".
 */
export function useClamped(ref: RefObject<HTMLElement | null>, deps: unknown[]): boolean {
  const [clamped, setClamped] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setClamped(el.scrollHeight > el.clientHeight + 1);
    measure();
    // jsdom computes no layout and the test setup stubs the observer, so a
    // missing implementation is normal rather than an error.
    if (typeof ResizeObserver !== 'function') return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
    // The ref identity is stable; the caller's deps are what move.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return clamped;
}
