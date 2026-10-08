import { useEffect, useLayoutEffect, useRef } from 'react';
import { useLayoutMode } from '../layout/useLayoutMode';
import { useAppStore } from '../store/store';
import { useLoc } from './useNav';

/**
 * Keeps the store's slots and open tabs in step with the **Location**.
 *
 * - A move to a view shows that view's pane, in the slot of its anchor and at the front of
 *   the recency, which the medium layout draws.
 * - A move to a panel tab opens it in the set and shows the panel. Before paint, so the
 *   panel never draws a location whose tab is not open yet.
 * - A move off the panel, as Back does, brings the medium layout's view back to the front.
 *
 * The narrow layout has no panel: its stack is drawn from the location alone.
 */
export function useLocSync() {
  const { view, panel } = useLoc();
  const mode = useLayoutMode();
  const showPane = useAppStore((s) => s.showPane);
  const openTab = useAppStore((s) => s.openTab);
  useEffect(() => showPane(view), [view, showPane]);

  const panelKey = panel?.key ?? null;
  const hadPanel = useRef(panelKey !== null);
  useLayoutEffect(() => {
    if (mode === 'narrow') return;
    if (panel) openTab(panel);
    else if (hadPanel.current && mode === 'medium') showPane(view);
    hadPanel.current = panel !== null;
    // On a move to another tab, not on every new location.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panelKey, mode]);
}
