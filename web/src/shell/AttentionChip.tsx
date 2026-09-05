import { useReactFlow } from '@xyflow/react';
import { useLayoutMode } from '../layout/useLayoutMode';
import { deskIdForTask } from '../protocol/deskId';
import { progressOf, zoneForState } from '../protocol/progress';
import { nextAttentionTask, openAttention } from '../selectors/attention';
import { agentsByTask, filteredTasks } from '../selectors/graph';
import { focusedTaskId } from '../selectors/tabs';
import { useAddToDesk } from '../api/desk';
import { useWorld } from '../api/useWorld';
import { useAppStore } from '../store/store';
import { AppButton } from '../ui/AppButton';
import styles from './AttentionChip.module.css';

/**
 * `⚠N` in the top bar. Clicking goes to the next node that needs the user.
 *
 * Two components, not one branch: `WideChip` calls `useReactFlow`, and the
 * narrow layout mounts no React Flow provider for it to read.
 */
export function AttentionChip() {
  return useLayoutMode() === 'narrow' ? <NarrowChip /> : <WideChip />;
}

/** The count, and what to do about it. Shared by both chips. */
function useAttention() {
  const { world } = useWorld();
  const filters = useAppStore((s) => s.ui.filters);
  // Counted over every task the filters allow, not only the drawn ones: the
  // desk opens empty, and an agent blocked on a task the user has not put on
  // it still needs them.
  const visible = new Set(filteredTasks(world, filters).map((t) => t.id));
  return { world, filters, visible, count: openAttention(world, visible).length };
}

/** The chip on a phone: it takes the deck list to the node and opens it. */
function NarrowChip() {
  const { world, visible, count } = useAttention();
  const stack = useAppStore((s) => s.ui.mobileStack);
  const pushScreen = useAppStore((s) => s.pushScreen);
  const setDeckZone = useAppStore((s) => s.setDeckZone);
  const view = useAppStore((s) => s.ui.view);
  const setView = useAppStore((s) => s.setView);
  const addToDesk = useAddToDesk();
  const top = stack[stack.length - 1];

  const go = async () => {
    const current = top?.kind === 'detail' ? top.nodeId : null;
    const next = nextAttentionTask(world, current, visible);
    if (!next) return;
    // The zone is read before the await, and from the task rather than the
    // deck: `deriveDeck` only holds what is already on the desk, so a task the
    // chip is about to add is not in it, and `world` here predates the add.
    const task = world.tasks[next];
    const agent = task ? agentsByTask(world).get(task.id) : undefined;
    const attention = Object.values(world.attention);
    setDeckZone(zoneForState(progressOf(task, agent, attention).state));
    if (!(deskIdForTask(next) in world.desk)) {
      await addToDesk.mutateAsync({ id: deskIdForTask(next) });
    }
    // From the task list, the deck has to be showing for Back to land on it.
    if (view !== 'canvas') setView('canvas');
    pushScreen({ kind: 'detail', nodeId: next });
  };

  return <Chip count={count} onClick={go} />;
}

/** The chip on a main monitor: it expands the node on the canvas. */
function WideChip() {
  const { world, visible, count } = useAttention();
  const tabs = useAppStore((s) => s.ui.tabs);
  const activeTabKey = useAppStore((s) => s.ui.activeTabKey);
  const expandedNodeId = useAppStore((s) => s.ui.expandedNodeId);
  const expandNode = useAppStore((s) => s.expandNode);
  const view = useAppStore((s) => s.ui.view);
  const setView = useAppStore((s) => s.setView);
  const { fitView } = useReactFlow();
  const addToDesk = useAddToDesk();

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

  return <Chip count={count} onClick={go} />;
}

/** The button both chips draw. */
function Chip({ count, onClick }: { count: number; onClick: () => void | Promise<void> }) {
  return (
    <AppButton
      className={styles.chip}
      data-testid="attention-chip"
      data-count={count}
      aria-label={`${count} items need attention`}
      onClick={onClick}
      disabled={count === 0}
    >
      ⚠ {count}
    </AppButton>
  );
}
