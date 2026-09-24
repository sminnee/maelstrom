import { useMemo } from 'react';
import { useWorld } from '../api/useWorld';
import type { Deck } from '../selectors/deck';
import { deriveDeck } from '../selectors/deck';
import { useAppStore } from '../store/store';

/**
 * The deck, derived once per world change rather than once per render.
 *
 * `deriveDeck` walks every task, agent and worktree, and three components read
 * it on a narrow render. The canvas memoises its own `deriveGraph` call for the
 * same reason (`canvas/Canvas.tsx`), and a phone is the weaker device.
 */
export function useDeck(): Deck & {
  byId: Map<string, Deck['zones'][keyof Deck['zones']][number]>;
} {
  const { world } = useWorld();
  const filters = useAppStore((s) => s.ui.filters);
  return useMemo(() => {
    const deck = deriveDeck(world, { filters });
    const byId = new Map(
      Object.values(deck.zones)
        .flat()
        .map((node) => [node.id, node] as const),
    );
    return { ...deck, byId };
  }, [world, filters]);
}
