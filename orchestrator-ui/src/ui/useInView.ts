import { useEffect, useState, type RefObject } from 'react';

/**
 * Whether the element has come within 200px of the screen. It latches: once
 * seen, it stays true, so a hunk scrolled past keeps its colour. Without
 * `IntersectionObserver` (jsdom) it is true from the start.
 */
export function useInView(ref: RefObject<Element | null>): boolean {
  const [seen, setSeen] = useState(() => typeof IntersectionObserver === 'undefined');
  useEffect(() => {
    const el = ref.current;
    if (seen || !el) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setSeen(true);
      },
      { rootMargin: '200px' },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, seen]);
  return seen;
}
