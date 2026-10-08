import { useMemo } from 'react';
import { showing } from '../selectors/slots';
import { useAppStore } from '../store/store';
import type { Pane } from '../store/uiSlice';
import { useLoc } from '../nav/useNav';
import { useLayoutMode } from './useLayoutMode';

/** The panes on screen in the layout being drawn, left first. */
export function useShowing(): Pane[] {
  const mode = useLayoutMode();
  const anchors = useAppStore((s) => s.ui.anchors);
  const slots = useAppStore((s) => s.ui.slots);
  const paneRecency = useAppStore((s) => s.ui.paneRecency);
  const { view } = useLoc();
  // Stable while the layout holds, so a caller can memoise on it.
  return useMemo(
    () => showing({ anchors, slots, paneRecency }, mode, view),
    [anchors, slots, paneRecency, mode, view],
  );
}
