import { useEffect, useState, type RefObject } from 'react';

/**
 * Whether the element is within 200px of the screen. By default it latches:
 * once seen, it stays true, so a hunk scrolled past keeps its colour. With
 * `latch: false` it follows the element out of view again, and `initial` is
 * its answer before the first report. Without `IntersectionObserver` (jsdom)
 * it is true from the start.
 */
export function useInView(
  ref: RefObject<Element | null>,
  { latch = true, initial = false }: { latch?: boolean; initial?: boolean } = {},
): boolean {
  const [seen, setSeen] = useState(() => typeof IntersectionObserver === 'undefined' || initial);
  const settled = latch && seen;
  useEffect(() => {
    const el = ref.current;
    if (settled || !el || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(
      (entries) => {
        const near = entries.some((entry) => entry.isIntersecting);
        if (near || !latch) setSeen(near);
      },
      { rootMargin: '200px' },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, settled, latch]);
  return seen;
}
