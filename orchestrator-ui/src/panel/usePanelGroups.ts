import { useWorld } from '../api/useWorld';
import { groupKeyOf, groupTabs } from '../selectors/tabs';
import { useAppStore } from '../store/store';
import type { PanelTab } from '../store/uiSlice';

/** The id of a sidebar row, so the panel it controls can name it. */
export const rowId = (groupKey: string) => `panel-row-${groupKey}`;

/**
 * The open tabs grouped by worktree, and the group in view: the active tab's.
 * No selection is stored for the group, so the sidebar and the strip cannot
 * disagree on which one it is.
 *
 * `Panel` calls this once and hands the result down: the grouping walks every
 * tab against the world, and one reading keeps the three parts in step.
 */
export function usePanelGroups() {
  const { world } = useWorld();
  const tabs = useAppStore((s) => s.ui.tabs);
  const activeTabKey = useAppStore((s) => s.ui.activeTabKey);
  const closeTabs = useAppStore((s) => s.closeTabs);
  const groups = groupTabs(world, tabs);
  const activeGroup =
    groups.flatMap((p) => p.worktrees).find((g) => g.tabs.some((t) => t.key === activeTabKey)) ??
    null;
  const groupOf = (tab: PanelTab) => groupKeyOf(world, tab);
  return {
    groups,
    activeGroup,
    /** Close tabs; the active one hands over within its own group first. */
    close: (keys: string[]) => closeTabs(keys, groupOf),
  };
}
