import { useRef } from 'react';

/** How far left a swipe from the edge travels before it opens the sheet. */
const OPEN_PX = 40;

/**
 * A leftward swipe in from the right edge, by touch. Spread the handlers on a
 * thin strip at the edge: the strip owns the gesture, so a row under it does
 * not swipe too. `onOpen` runs at 40px, with the finger still down. The sheet
 * does not follow the finger: its own keyframe plays.
 */
export function useEdgeSwipe(onOpen: () => void) {
  const start = useRef<{ pointerId: number; x: number; y: number } | null>(null);
  return {
    onPointerDown: (e: React.PointerEvent<HTMLElement>) => {
      if (e.pointerType !== 'touch' || !e.isPrimary) return;
      start.current = { pointerId: e.pointerId, x: e.clientX, y: e.clientY };
      e.currentTarget.setPointerCapture?.(e.pointerId);
    },
    onPointerMove: (e: React.PointerEvent<HTMLElement>) => {
      const s = start.current;
      if (!s || e.pointerId !== s.pointerId) return;
      const dx = e.clientX - s.x;
      if (-dx >= OPEN_PX && -dx > Math.abs(e.clientY - s.y)) {
        start.current = null;
        onOpen();
      }
    },
    onPointerUp: () => {
      start.current = null;
    },
    onPointerCancel: () => {
      start.current = null;
    },
  };
}
