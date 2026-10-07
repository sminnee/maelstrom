import { useMemo } from 'react';
import { showing } from '../selectors/slots';
import { useAppStore } from '../store/store';
import type { Pane } from '../store/uiSlice';
import { useLayoutMode } from './useLayoutMode';

/** The panes on screen in the layout being drawn, left first. */
export function useShowing(): Pane[] {
  const mode = useLayoutMode();
  const anchors = useAppStore((s) => s.ui.anchors);
  const slots = useAppStore((s) => s.ui.slots);
  const paneRecency = useAppStore((s) => s.ui.paneRecency);
  // Stable while the layout holds, so a caller can memoise on it.
  return useMemo(
    () => showing({ anchors, slots, paneRecency }, mode),
    [anchors, slots, paneRecency, mode],
  );
}
