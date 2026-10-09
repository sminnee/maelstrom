import { useRef } from 'react';

/** How far a pointer moves before the drag picks an axis. */
const AXIS_LOCK_PX = 8;

/** A sideways drag in progress, as the callbacks see it. */
export interface SidewaysDrag {
  /** Distance from the press, in px. Negative is left. */
  dx: number;
  /** The element's width at the press. */
  width: number;
  /** The speed of the last move, in px per ms. Negative is left. */
  speed: number;
}

interface Tracked {
  pointerId: number;
  x: number;
  y: number;
  sideways: boolean;
  width: number;
  lastX: number;
  lastT: number;
  speed: number;
}

/**
 * A sideways pointer drag, with a vertical one let go so the page scrolls.
 *
 * The drag picks an axis after 8px. Vertical: the hook lets go, and nothing
 * runs. Sideways: it captures the pointer, then `onStart`, `onMove` on each
 * move, and `onRelease` or `onCancel` at the end. A second pointer is ignored.
 * A control under the press keeps its tap, because a tap moves less than 8px.
 *
 * Spread the handlers on the element that moves.
 */
export function useAxisDrag({
  canStart = () => true,
  onStart,
  onMove,
  onRelease,
  onCancel,
}: {
  /** Whether a press may begin a drag now. */
  canStart?: () => boolean;
  onStart?: (drag: SidewaysDrag) => void;
  onMove: (drag: SidewaysDrag) => void;
  onRelease: (drag: SidewaysDrag) => void;
  onCancel: () => void;
}) {
  const tracked = useRef<Tracked | null>(null);
  const view = (t: Tracked, x: number): SidewaysDrag => ({
    dx: x - t.x,
    width: t.width,
    speed: t.speed,
  });
  return {
    onPointerDown: (e: React.PointerEvent<HTMLElement>) => {
      if (!e.isPrimary || e.button > 0 || !canStart()) return;
      tracked.current = {
        pointerId: e.pointerId,
        x: e.clientX,
        y: e.clientY,
        sideways: false,
        width: e.currentTarget.getBoundingClientRect().width,
        lastX: e.clientX,
        lastT: e.timeStamp,
        speed: 0,
      };
    },
    onPointerMove: (e: React.PointerEvent<HTMLElement>) => {
      const t = tracked.current;
      if (!t || e.pointerId !== t.pointerId) return;
      if (!t.sideways) {
        const mx = Math.abs(e.clientX - t.x);
        const my = Math.abs(e.clientY - t.y);
        if (Math.max(mx, my) < AXIS_LOCK_PX) return;
        if (my >= mx) {
          tracked.current = null;
          return;
        }
        t.sideways = true;
        e.currentTarget.setPointerCapture?.(e.pointerId);
        onStart?.(view(t, e.clientX));
      }
      const dt = e.timeStamp - t.lastT;
      if (dt > 0) t.speed = (e.clientX - t.lastX) / dt;
      t.lastX = e.clientX;
      t.lastT = e.timeStamp;
      onMove(view(t, e.clientX));
    },
    onPointerUp: (e: React.PointerEvent<HTMLElement>) => {
      const t = tracked.current;
      if (!t || e.pointerId !== t.pointerId) return;
      tracked.current = null;
      if (t.sideways) onRelease(view(t, e.clientX));
    },
    onPointerCancel: (e: React.PointerEvent<HTMLElement>) => {
      const t = tracked.current;
      if (!t || e.pointerId !== t.pointerId) return;
      tracked.current = null;
      if (t.sideways) onCancel();
    },
  };
}
