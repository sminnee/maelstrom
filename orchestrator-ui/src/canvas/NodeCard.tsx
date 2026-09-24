import { useEffect, useLayoutEffect, useRef } from 'react';
import { ViewportPortal, useReactFlow } from '@xyflow/react';
import type { GraphNode } from '../selectors/graph';
import { useAppStore } from '../store/store';
import { nodeTitle } from '../selectors/graph';
import { NodeCardBody } from './NodeCardBody';
import { NODE } from './layout';
import styles from './NodeCard.module.css';

/** The card's width in flow units. Its height comes from its content. */
export const CARD_WIDTH = 440;

const EASE = 'cubic-bezier(0.16, 1, 0.3, 1)';
const GROW_MS = 260;
const SHRINK_MS = 180;
const FADE_MS = 120;

/**
 * The expanded node's card, in React Flow's viewport portal so it pans and
 * zooms with the canvas. `open` false plays the collapse, then calls `onClosed`.
 */
export function NodeCard({
  node,
  position,
  open,
  onClosed,
}: {
  node: GraphNode;
  position: { x: number; y: number };
  open: boolean;
  onClosed: () => void;
}) {
  const collapseNode = useAppStore((s) => s.collapseNode);
  const { getViewport, setViewport } = useReactFlow();
  const card = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);

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

  // Grow from the node's size to the card's measured size; the content fades in after.
  useLayoutEffect(() => {
    const el = card.current;
    const body = inner.current;
    el?.focus({ preventScroll: true });
    if (!el || !body || typeof el.animate !== 'function') return;
    if (reducedMotion()) {
      el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: FADE_MS });
      return;
    }
    el.animate(
      [
        { width: `${NODE.width}px`, height: `${NODE.height}px` },
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
    const to = reducedMotion()
      ? { opacity: 0 }
      : { width: `${NODE.width}px`, height: `${NODE.height}px`, opacity: 0 };
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
      collapseNode();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [collapseNode]);

  const title = nodeTitle(node);

  return (
    <ViewportPortal>
      <div
        ref={card}
        className={`${styles.card} nowheel nopan nodrag`}
        role="dialog"
        aria-label={title}
        tabIndex={-1}
        data-phase={node.phase ?? undefined}
        data-state={node.progress.state}
        style={{ transform: `translate(${position.x}px, ${position.y}px)` }}
      >
        <div ref={inner} className={styles.inner}>
          <NodeCardBody
            node={node}
            onDone={collapseNode}
            closeControl={
              <button
                type="button"
                className={styles.close}
                aria-label="Collapse"
                onClick={collapseNode}
              >
                ×
              </button>
            }
          />
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
