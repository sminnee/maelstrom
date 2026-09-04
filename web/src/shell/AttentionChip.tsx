import { useReactFlow } from '@xyflow/react';
import { deskIdForTask } from '../protocol/deskId';
import { nextAttentionTask, openAttention } from '../selectors/attention';
import { filteredTasks } from '../selectors/graph';
import { focusedTaskId } from '../selectors/tabs';
import { useAddToDesk } from '../api/desk';
import { useWorld } from '../api/useWorld';
import { useAppStore } from '../store/store';
import { AppButton } from '../ui/AppButton';
import styles from './AttentionChip.module.css';

/** `⚠N` in the top bar. Clicking expands the next node that needs the user and fits the view to it. */
export function AttentionChip() {
  const { world } = useWorld();
  const tabs = useAppStore((s) => s.ui.tabs);
  const activeTabKey = useAppStore((s) => s.ui.activeTabKey);
  const filters = useAppStore((s) => s.ui.filters);
  const expandedNodeId = useAppStore((s) => s.ui.expandedNodeId);
  const expandNode = useAppStore((s) => s.expandNode);
  const view = useAppStore((s) => s.ui.view);
  const setView = useAppStore((s) => s.setView);
  const { fitView } = useReactFlow();
  const addToDesk = useAddToDesk();
  // Counted over every task the filters allow, not only the drawn ones: the
  // desk opens empty, and an agent blocked on a task the user has not put on
  // it still needs them.
  const visible = new Set(filteredTasks(world, filters).map((t) => t.id));
  const count = openAttention(world, visible).length;

  const go = async () => {
    const current = expandedNodeId ?? focusedTaskId(world, tabs, activeTabKey);
    const next = nextAttentionTask(world, current, visible);
    if (!next) return;
    // The canvas draws the desk, so a task off it has no node to expand.
    if (!(deskIdForTask(next) in world.desk)) {
      await addToDesk.mutateAsync({ id: deskIdForTask(next) });
    }
    // From the task list, the canvas has to be showing before it can be
    // fitted, so the fit waits for the frame that draws it.
    if (view !== 'canvas') setView('canvas');
    expandNode(next, false);
    requestAnimationFrame(() => {
      void fitView({ nodes: [{ id: next }], duration: 300, maxZoom: 1.2 });
    });
  };

  return (
    <AppButton
      className={styles.chip}
      data-testid="attention-chip"
      data-count={count}
      aria-label={`${count} items need attention`}
      onClick={go}
      disabled={count === 0}
    >
      ⚠ {count}
    </AppButton>
  );
}
