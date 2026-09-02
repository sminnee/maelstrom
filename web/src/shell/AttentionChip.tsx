import { useReactFlow } from '@xyflow/react';
import { nextAttentionTask, openAttention } from '../selectors/attention';
import { deriveGraph } from '../selectors/graph';
import { focusedTaskId, summaryTab } from '../selectors/tabs';
import { useAppStore } from '../store/store';
import styles from './AttentionChip.module.css';

/** `⚠N` in the top bar. Clicking fits the view to the next node that needs the user. */
export function AttentionChip() {
  const world = useAppStore((s) => s.world);
  const tabs = useAppStore((s) => s.ui.tabs);
  const activeTabKey = useAppStore((s) => s.ui.activeTabKey);
  const groupBy = useAppStore((s) => s.ui.groupBy);
  const filters = useAppStore((s) => s.ui.filters);
  const openTab = useAppStore((s) => s.openTab);
  const { fitView } = useReactFlow();
  const visible = new Set(deriveGraph(world, { groupBy, filters }).nodes.map((n) => n.id));
  const count = openAttention(world, visible).length;

  const go = () => {
    const next = nextAttentionTask(world, focusedTaskId(world, tabs, activeTabKey), visible);
    if (!next) return;
    openTab(summaryTab(next));
    void fitView({ nodes: [{ id: next }], duration: 300, maxZoom: 1.2 });
  };

  return (
    <button
      type="button"
      className={styles.chip}
      data-testid="attention-chip"
      data-count={count}
      aria-label={`${count} items need attention`}
      onClick={go}
      disabled={count === 0}
    >
      ⚠ {count}
    </button>
  );
}
