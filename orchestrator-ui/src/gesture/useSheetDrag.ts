import { useEffect, useRef, useState } from 'react';
import { useAxisDrag } from './useAxisDrag';

/** The least drag that closes the sheet, and the whole of it where the sheet has no width. */
const MIN_CLOSE_PX = 80;
/** The share of the sheet's width a drag must pass to close it. */
const CLOSE_SHARE = 0.3;
/** A flick this fast closes the sheet short of the distance, in px per ms. */
const FLICK_SPEED = 0.5;
/** A flick still has to travel this far, so a tap's jitter is no flick. */
const FLICK_MIN_PX = 40;
/** The slide-out's length. `onClose` runs at its end, or after this where no transition ends. */
const SLIDE_MS = 200;

/**
 * A drag right on the side sheet that closes it. The sheet follows the finger,
 * and the backdrop lightens with it. A release past 30% of the width, or a
 * flick, slides it out; anything less springs back.
 */
export function useSheetDrag(onClose: () => void) {
  const [dx, setDx] = useState(0);
  const [width, setWidth] = useState(0);
  const [phase, setPhase] = useState<'rest' | 'drag' | 'closing'>('rest');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const rest = () => {
    setPhase('rest');
    setDx(0);
  };

  // Once, from `transitionend` or the timer. `onClose` may keep the dialog
  // open, to ask about unsaved work first, so the sheet comes back to rest.
  const close = () => {
    if (!timer.current) return;
    clearTimeout(timer.current);
    timer.current = null;
    rest();
    onClose();
  };

  const drag = useAxisDrag({
    canStart: () => phase !== 'closing',
    onStart: (d) => {
      setWidth(d.width);
      setPhase('drag');
    },
    onMove: (d) => setDx(Math.max(0, d.dx)),
    onRelease: (d) => {
      const far = d.dx >= Math.max(MIN_CLOSE_PX, d.width * CLOSE_SHARE);
      const flick = d.dx >= FLICK_MIN_PX && d.speed >= FLICK_SPEED;
      if (!far && !flick) return rest();
      setPhase('closing');
      // Where nothing animates — reduced motion, or a test — no transition ends.
      timer.current = setTimeout(close, SLIDE_MS + 50);
    },
    onCancel: rest,
  });

  return {
    /** Inline style for the sheet: its offset, and the backdrop's share of dark. */
    style:
      phase === 'rest'
        ? undefined
        : ({
            transform: phase === 'closing' ? 'translateX(100%)' : `translateX(${dx}px)`,
            '--sheet-dim': phase === 'closing' ? 0 : width ? Math.max(0, 1 - dx / width) : 1,
          } as React.CSSProperties),
    /** Whether the finger moves the sheet now. Draw it with no transition then. */
    dragging: phase === 'drag',
    handlers: {
      ...drag,
      onTransitionEnd: (e: React.TransitionEvent<HTMLElement>) => {
        if (e.target === e.currentTarget) close();
      },
    },
  };
}
