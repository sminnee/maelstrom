import { useCallback } from 'react';
import type { GraphNode } from '../selectors/graph';
import { cardNodeId, cardWorktreeId, type Card } from './location';
import { useGo, useLoc, type GoOptions } from './useNav';

/** The card a node opens: its task's, or a free agent's. */
export const cardOf = (node: Pick<GraphNode, 'id' | 'kind'>): Card => ({
  kind: node.kind === 'task' ? 'task' : 'agent',
  id: node.id,
});

const sameCard = (a: Card | null, b: Card) => a?.kind === b.kind && a.id === b.id;

/**
 * The card the location opens, and the moves that open and close it. One card at a time:
 * opening a node's card closes a worktree's, and the reverse.
 */
export function useCard() {
  const { card } = useLoc();
  const go = useGo();
  /** Open a card. With `toggle`, opening the open one closes it. */
  const open = useCallback(
    (next: Card, toggle = true) =>
      toggle && sameCard(card, next) ? go({ card: null }, { close: true }) : go({ card: next }),
    [card, go],
  );
  /** Close the open card, if there is one. Closing goes back to where it was opened from. */
  const collapse = useCallback(
    (opts: GoOptions = { close: true }) => {
      if (card) go({ card: null }, opts);
    },
    [card, go],
  );
  return {
    card,
    expandedNodeId: cardNodeId(card),
    expandedWorktreeId: cardWorktreeId(card),
    open,
    collapse,
  };
}
