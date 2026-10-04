import { useMemo } from 'react';
import { useReactFlow } from '@xyflow/react';
import { useLayoutMode } from '../layout/useLayoutMode';
import { useShowing } from '../layout/useShowing';
import { zoneForState } from '../protocol/progress';
import { attentionNodes, nextAttentionNode, unansweredNodes } from '../selectors/attention';
import { deriveGraph } from '../selectors/graph';
import { focusedTaskId } from '../selectors/tabs';
import { useWorld } from '../api/useWorld';
import { useAppStore } from '../store/store';
import { AppButton } from '../ui/AppButton';
import styles from './AttentionChip.module.css';

/**
 * `⚠N` in the top bar, and beside it the count of unanswered agents. Clicking
 * goes to the next node that needs the user: the asks first, then the
 * unanswered.
 *
 * Two components, not one branch: `WideChip` calls `useReactFlow`, and the
 * narrow layout mounts no React Flow provider for it to read.
 */
export function AttentionChip({ hideWhenClear = false }: { hideWhenClear?: boolean }) {
  return useLayoutMode() === 'narrow' ? <NarrowChip hideWhenClear={hideWhenClear} /> : <WideChip />;
}

/**
 * The drawn nodes and the two chip counts. Shared by both chips. Memoised as the
 * canvas and the deck memoise their own `deriveGraph` calls.
 */
function useAttention() {
  const { world } = useWorld();
  const filters = useAppStore((s) => s.ui.filters);
  // Grouping moves a node between lanes and changes neither its state nor
  // whether it draws, so the chip does not follow it.
  const nodes = useMemo(() => deriveGraph(world, { filters }).nodes, [world, filters]);
  return {
    world,
    nodes,
    count: attentionNodes(nodes).length,
    unanswered: unansweredNodes(nodes).length,
  };
}

/** The chip on a phone: it takes the deck list to the node and opens it. */
function NarrowChip({ hideWhenClear }: { hideWhenClear: boolean }) {
  const { nodes, count, unanswered } = useAttention();
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
    // Back from the detail screen lands on the list that holds the node. An
    // unanswered node sits in the same zone as an ask.
    setDeckZone(zoneForState('needs-attention'));
    // From the task list, the deck has to be showing for Back to land on it.
    if (!deckShowing) showPane('canvas');
    pushScreen({ kind: 'detail', nodeId: next });
  };

  if (hideWhenClear && count === 0 && unanswered === 0) return null;
  return <Chip count={count} unanswered={unanswered} onClick={go} />;
}

/** The chip on a main monitor: it expands the node on the canvas. */
function WideChip() {
  const { world, nodes, count, unanswered } = useAttention();
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

  return <Chip count={count} unanswered={unanswered} onClick={go} />;
}

/** The button both chips draw. */
function Chip({
  count,
  unanswered,
  onClick,
}: {
  count: number;
  unanswered: number;
  onClick: () => void;
}) {
  const label = [
    `${count} items need attention`,
    unanswered > 0 && `${unanswered} ${unanswered === 1 ? 'agent' : 'agents'} unanswered`,
  ]
    .filter(Boolean)
    .join(', ');
  return (
    <AppButton
      className={styles.chip}
      data-testid="attention-chip"
      data-count={count}
      aria-label={label}
      onClick={onClick}
      disabled={count === 0 && unanswered === 0}
    >
      <span className={styles.asks} data-testid="attention-count">
        ⚠ {count}
      </span>
      {unanswered > 0 && (
        <span className={styles.unanswered} data-testid="unanswered-count">
          {unanswered}
        </span>
      )}
    </AppButton>
  );
}
