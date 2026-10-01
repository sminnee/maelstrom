import { useMemo } from 'react';
import { useReactFlow } from '@xyflow/react';
import { useLayoutMode } from '../layout/useLayoutMode';
import { useShowing } from '../layout/useShowing';
import { zoneForState } from '../protocol/progress';
import { attentionNodes, nextAttentionNode } from '../selectors/attention';
import { deriveGraph } from '../selectors/graph';
import { focusedTaskId } from '../selectors/tabs';
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

/**
 * The drawn nodes and the chip count. Shared by both chips. Memoised as the
 * canvas and the deck memoise their own `deriveGraph` calls.
 */
function useAttention() {
  const { world } = useWorld();
  const filters = useAppStore((s) => s.ui.filters);
  // Grouping moves a node between lanes and changes neither its state nor
  // whether it draws, so the chip does not follow it.
  const nodes = useMemo(
    () => deriveGraph(world, { filters, groupBy: 'none' }).nodes,
    [world, filters],
  );
  return { world, nodes, count: attentionNodes(nodes).length };
}

/** The chip on a phone: it takes the deck list to the node and opens it. */
function NarrowChip() {
  const { nodes, count } = useAttention();
  const stack = useAppStore((s) => s.ui.mobileStack);
  const pushScreen = useAppStore((s) => s.pushScreen);
  const setDeckZone = useAppStore((s) => s.setDeckZone);
  const deckShowing = useShowing().includes('canvas');
  const showPane = useAppStore((s) => s.showPane);
  const top = stack[stack.length - 1];

  const go = () => {
    const current = top?.kind === 'detail' ? top.nodeId : null;
    const next = nextAttentionNode(nodes, current);
    if (!next) return;
    // Back from the detail screen lands on the list that holds the node.
    setDeckZone(zoneForState('needs-attention'));
    // From the task list, the deck has to be showing for Back to land on it.
    if (!deckShowing) showPane('canvas');
    pushScreen({ kind: 'detail', nodeId: next });
  };

  return <Chip count={count} onClick={go} />;
}

/** The chip on a main monitor: it expands the node on the canvas. */
function WideChip() {
  const { world, nodes, count } = useAttention();
  const tabs = useAppStore((s) => s.ui.tabs);
  const activeTabKey = useAppStore((s) => s.ui.activeTabKey);
  const expandedNodeId = useAppStore((s) => s.ui.expandedNodeId);
  const expandNode = useAppStore((s) => s.expandNode);
  const canvasShowing = useShowing().includes('canvas');
  const showPane = useAppStore((s) => s.showPane);
  const { fitView } = useReactFlow();

  const go = () => {
    const current = expandedNodeId ?? focusedTaskId(world, tabs, activeTabKey);
    const next = nextAttentionNode(nodes, current);
    if (!next) return;
    // The canvas has to be showing before it can be fitted, so the fit waits
    // for the frame that draws it.
    if (!canvasShowing) showPane('canvas');
    expandNode(next, false);
    requestAnimationFrame(() => {
      void fitView({ nodes: [{ id: next }], duration: 300, maxZoom: 1.2 });
    });
  };

  return <Chip count={count} onClick={go} />;
}

/** The button both chips draw. */
function Chip({ count, onClick }: { count: number; onClick: () => void }) {
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
