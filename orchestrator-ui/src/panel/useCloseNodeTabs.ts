import { useWorld } from '../api/useWorld';
import { nodeTabKeys, type TabNode } from '../selectors/tabs';
import { useAppStore } from '../store/store';
import { useCloseTabs } from './usePanelNav';

/**
 * Close the tabs of nodes that leave the desk: see docs/dev/orchestrator-ui.md. One close for
 * all of them, so the active tab hands over once.
 */
export function useCloseNodeTabs(): (...nodes: TabNode[]) => void {
  const { world } = useWorld();
  const close = useCloseTabs();
  return (...nodes) => {
    const { tabs } = useAppStore.getState().ui;
    const keys = [...new Set(nodes.flatMap((node) => nodeTabKeys(world, tabs, node)))];
    if (keys.length > 0) close(keys);
  };
}
