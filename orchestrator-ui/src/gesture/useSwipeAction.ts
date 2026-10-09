import { useRef, useState } from 'react';
import { useClickLifecycle } from '../ui/useClickLifecycle';
import { haptic } from './haptic';
import { useAxisDrag } from './useAxisDrag';

/** The least distance that arms a swipe, and the whole of it where a row has no width. */
const MIN_THRESHOLD_PX = 80;
/** The share of the row's width that arms a swipe on a wide row. */
const THRESHOLD_SHARE = 0.35;

const thresholdFor = (width: number) => Math.max(MIN_THRESHOLD_PX, width * THRESHOLD_SHARE);

/**
 * A left swipe that runs `action` on release past a threshold. Before it, a
 * release springs back. Crossing the threshold arms the swipe and ticks the
 * haptic. A vertical drag lets go, so the list scrolls.
 *
 * Spread `handlers` on the sliding layer, and draw it at `offset` px. While the
 * action is pending, the row holds open at the threshold. After it fails, the
 * row opens its whole width, so the reveal can show the reason.
 */
export function useSwipeAction(action: (() => Promise<unknown>) | null) {
  const [dx, setDx] = useState(0);
  const [armed, setArmed] = useState(false);
  const [dragging, setDragging] = useState(false);
  // The row's width at the last release, which sets where it holds after.
  const [width, setWidth] = useState(0);
  // Read in the same event that sets it, so a ref: state would be a render late.
  const armedNow = useRef(false);
  // A drag ends in a click on the link under it. One that moved sideways must not open it.
  const swallowClick = useRef(false);
  const { state, run } = useClickLifecycle();

  const end = () => {
    armedNow.current = false;
    setDragging(false);
    setDx(0);
    setArmed(false);
  };

  const drag = useAxisDrag({
    canStart: () => !!action && state.kind === 'ready',
    onStart: () => {
      swallowClick.current = true;
      setDragging(true);
    },
    onMove: (d) => {
      const next = Math.min(0, d.dx);
      const nowArmed = -next >= thresholdFor(d.width);
      if (nowArmed && !armedNow.current) haptic();
      armedNow.current = nowArmed;
      setArmed(nowArmed);
      setDx(next);
    },
    onRelease: (d) => {
      if (armedNow.current && action) {
        setWidth(d.width);
        void run(action);
      }
      end();
    },
    onCancel: end,
  });

  const handlers = {
    ...drag,
    onPointerDown: (e: React.PointerEvent<HTMLElement>) => {
      swallowClick.current = false;
      drag.onPointerDown(e);
    },
    onClickCapture: (e: React.MouseEvent<HTMLElement>) => {
      if (!swallowClick.current) return;
      swallowClick.current = false;
      e.preventDefault();
      e.stopPropagation();
    },
  };

  const threshold = thresholdFor(width);
  const offset =
    state.kind === 'error'
      ? -Math.max(threshold, width)
      : state.kind === 'processing'
        ? -threshold
        : dx;
  return {
    /** The sliding layer's offset, in px: zero or less. */
    offset,
    /** Past the threshold, or acting on it. */
    armed: armed || state.kind !== 'ready',
    /** While the finger moves the row. Draw it with no transition then. */
    dragging,
    state,
    handlers,
  };
}
