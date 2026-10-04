import type { GraphNode } from '../selectors/graph';
import { useAppStore } from '../store/store';
import { nodeTitle } from '../selectors/graph';
import { CanvasCard } from './CanvasCard';
import { NodeCardBody } from './NodeCardBody';
import { NODE } from './layout';
import { actionIcon } from '../ui/actionIcons';
import styles from './NodeCard.module.css';

/** The card's width in flow units. Its height comes from its content. */
export const CARD_WIDTH = 440;

/** The expanded node's card. It grows from the node it stands for. */
export function NodeCard({
  node,
  position,
  open,
  onClosed,
}: {
  node: GraphNode;
  position: { x: number; y: number };
  open: boolean;
  onClosed: () => void;
}) {
  const collapseCard = useAppStore((s) => s.collapseCard);
  return (
    <CanvasCard
      label={nodeTitle(node)}
      className={styles.card}
      position={position}
      from={NODE}
      open={open}
      onClosed={onClosed}
      phase={node.phase ?? undefined}
      state={node.progress.state}
    >
      <NodeCardBody
        node={node}
        onDone={collapseCard}
        closeControl={
          <button
            type="button"
            className={styles.close}
            aria-label="Collapse"
            onClick={collapseCard}
          >
            {actionIcon('close')}
          </button>
        }
      />
    </CanvasCard>
  );
}
