import { useEffect, useLayoutEffect, useRef, type ReactNode } from 'react';
import { ViewportPortal, useReactFlow } from '@xyflow/react';
import { useAppStore } from '../store/store';
import styles from './CanvasCard.module.css';

const EASE = 'cubic-bezier(0.16, 1, 0.3, 1)';
const GROW_MS = 260;
const SHRINK_MS = 180;
const FADE_MS = 120;

/**
 * The shell of a card on the canvas: a node's card and a worktree's card are
 * both this, round their own content.
 *
 * It sits in React Flow's viewport portal, so it pans and zooms with the
 * canvas. It grows from `from`, the size of what was clicked, and pans into
 * view when it runs past the canvas edge. Esc collapses it. `open` false plays
 * the collapse, then calls `onClosed`.
 *
 * `className` is the caller's own card class, which composes `card` from
 * `CanvasCard.module.css` and sets the width.
 */
export function CanvasCard({
  label,
  className,
  position,
  from,
  open,
  onClosed,
  phase,
  state,
  children,
}: {
  /** The dialog's accessible name. */
  label: string;
  className: string | undefined;
  position: { x: number; y: number };
  from: { width: number; height: number };
  open: boolean;
  onClosed: () => void;
  phase?: string;
  state?: string;
  children: ReactNode;
}) {
  const collapse = useAppStore((s) => s.collapseCard);
  const { getViewport, setViewport } = useReactFlow();
  const card = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  // Read once: a card grows from what was clicked, and that does not change.
  const start = useRef(from);

  // A card that runs past the canvas edge pans into view. It measures the
  // laid-out box (the grow animation only plays towards it), and again
  // whenever the content changes the card's size.
  useLayoutEffect(() => {
    const el = card.current;
    const pane = el?.closest('.react-flow');
    if (!el || !pane || typeof el.getBoundingClientRect !== 'function') return;
    const intoView = () => {
      const box = el.getBoundingClientRect();
      const edge = pane.getBoundingClientRect();
      if (box.width === 0 || box.height === 0) return;
      const margin = 16;
      const dx = Math.min(0, edge.right - margin - box.right);
      const dy = Math.min(0, edge.bottom - margin - box.bottom);
      if (dx === 0 && dy === 0) return;
      const { x, y, zoom } = getViewport();
      void setViewport({ x: x + dx, y: y + dy, zoom }, { duration: 300 });
    };
    intoView();
    if (typeof ResizeObserver !== 'function') return;
    const observer = new ResizeObserver(intoView);
    observer.observe(el);
    return () => observer.disconnect();
  }, [getViewport, setViewport]);

  // Grow from the clicked thing's size to the card's measured size; the content fades in after.
  useLayoutEffect(() => {
    const el = card.current;
    const body = inner.current;
    el?.focus({ preventScroll: true });
    if (!el || !body || typeof el.animate !== 'function') return;
    if (reducedMotion()) {
      el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: FADE_MS });
      return;
    }
    const { width, height } = start.current;
    el.animate(
      [
        { width: `${width}px`, height: `${height}px` },
        { width: `${el.offsetWidth}px`, height: `${el.offsetHeight}px` },
      ],
      { duration: GROW_MS, easing: EASE },
    );
    body.animate([{ opacity: 0 }, { opacity: 1 }], {
      duration: GROW_MS - FADE_MS,
      delay: FADE_MS,
      fill: 'backwards',
    });
  }, []);

  // Collapse reverses the grow, then the card unmounts.
  useEffect(() => {
    if (open) return;
    const el = card.current;
    if (!el || typeof el.animate !== 'function') {
      onClosed();
      return;
    }
    const { width, height } = start.current;
    const to = reducedMotion()
      ? { opacity: 0 }
      : { width: `${width}px`, height: `${height}px`, opacity: 0 };
    const animation = el.animate([{ opacity: 1 }, to], {
      duration: SHRINK_MS,
      easing: 'ease-in',
      fill: 'forwards',
    });
    let live = true;
    const done = () => {
      if (live) onClosed();
    };
    animation.finished.then(done, done);
    // Reopened mid-collapse: keep the card rather than unmounting it.
    return () => {
      live = false;
      animation.cancel();
    };
  }, [open, onClosed]);

  // Esc collapses the card, unless the panel is handling it (a composer, an input).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if ((e.target as Element | null)?.closest?.('[data-testid="panel"]')) return;
      collapse();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [collapse]);

  return (
    <ViewportPortal>
      <div
        ref={card}
        className={`${className ?? ''} nowheel nopan nodrag`}
        role="dialog"
        aria-label={label}
        tabIndex={-1}
        data-phase={phase}
        data-state={state}
        style={{ transform: `translate(${position.x}px, ${position.y}px)` }}
      >
        <div ref={inner} className={styles.inner}>
          {children}
        </div>
      </div>
    </ViewportPortal>
  );
}

function reducedMotion(): boolean {
  return (
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}
