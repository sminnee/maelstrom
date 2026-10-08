import { useCallback } from 'react';
import { useWorld } from '../api/useWorld';
import { useGo, useLoc, type GoOptions } from '../nav/useNav';
import { activateTab, groupKeyOf, selectGroup as selectGroupIn } from '../selectors/tabs';
import { useAppStore } from '../store/store';
import type { PanelTab } from '../store/uiSlice';

/**
 * The active tab, which the location names, and the moves that change it. The store keeps
 * the open set; a move to a tab goes through the URL, so Back returns to the tab before.
 */
export function usePanelNav() {
  const { world } = useWorld();
  const { panel } = useLoc();
  const go = useGo();
  const ui = useAppStore((s) => s.ui);
  const closeTabs = useAppStore((s) => s.closeTabs);
  const toggleSplit = useAppStore((s) => s.toggleSplit);
  const activeTabKey = panel?.key ?? null;
  const groupOf = useCallback((tab: PanelTab) => groupKeyOf(world, tab), [world]);

  /** Move the location to a tab, or to none. A move to the active tab is no move. */
  const show = (key: string | null, opts?: GoOptions) => {
    if (key === activeTabKey) return;
    go({ panel: ui.tabs.find((t) => t.key === key) ?? null }, opts);
  };

  return {
    activeTabKey,
    /** A click on a tab of the strip. The split tab is already showing, so it stays where it is. */
    activate: (key: string) => show(activateTab({ ...ui, activeTabKey }, key).activeTabKey),
    /** A shift-click on a tab: put it beside the active one, or take it back out. */
    split: (key: string) => show(toggleSplit(key, groupOf, activeTabKey)),
    /** Show a worktree group: the most recent of its tabs, past its split tab. */
    selectGroup: (tabKeys: string[]) =>
      show(selectGroupIn({ ...ui, activeTabKey }, tabKeys).activeTabKey),
    /**
     * Close tabs; the active one hands over within its own group first. The move closes: it
     * goes back when the tab that takes over is the entry before, and replaces otherwise.
     */
    close: (keys: string[]) => show(closeTabs(keys, groupOf, activeTabKey), { close: true }),
  };
}
