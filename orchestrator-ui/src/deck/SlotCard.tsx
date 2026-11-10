import { useEffect, useRef } from 'react';
import { useOwnHost } from '../api/host';
import { useWorld } from '../api/useWorld';
import { NodeCardBody } from '../canvas/NodeCardBody';
import { useCard } from '../nav/useCard';
import { nodeTitle } from '../selectors/graph';
import { useEscape } from '../ui/useEscape';
import { useDeck } from './useDeck';
import { CollapseButton } from '../canvas/CollapseButton';
import styles from './SlotCard.module.css';

/** The open node's card in kanban and list mode: fixed to the right edge of the desk slot, over the view. */
export function SlotCard() {
  const { status } = useWorld();
  const { expandedNodeId, collapse } = useCard();
  const node = useDeck().byId.get(expandedNodeId ?? '');
  const { idPrefix } = useOwnHost();
  const card = useRef<HTMLDivElement>(null);

  // A node that no longer draws collapses its card, as on the canvas. Replace, not push: Back
  // must not return to a card that cannot draw.
  const gone = status === 'ready' && expandedNodeId !== null && !node;
  useEffect(() => {
    if (gone) collapse({ replace: true });
  }, [gone, collapse]);

  const shown = node !== undefined;
  useEffect(() => {
    if (shown) card.current?.focus({ preventScroll: true });
  }, [shown]);
  useEscape(collapse, shown);

  if (!node) return null;
  return (
    <div
      ref={card}
      className={styles.card}
      role="dialog"
      aria-label={nodeTitle(node, idPrefix)}
      tabIndex={-1}
      data-testid="slot-card"
      data-phase={node.phase ?? undefined}
      data-state={node.progress.state}
    >
      <div className={styles.inner}>
        <NodeCardBody node={node} onDone={() => collapse()} closeControl={<CollapseButton />} />
      </div>
    </div>
  );
}
