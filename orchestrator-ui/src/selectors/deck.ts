import type { Zone } from '../protocol/progress';
import { ZONES, zoneForState } from '../protocol/progress';
import type { WorldView } from './world';
import type { Filters } from './filters';
import { deriveGraph, type GraphNode } from './graph';

export interface DeckOptions {
  filters: Filters;
}

export interface Deck {
  /** The drawn nodes, bucketed by zone. */
  zones: Record<Zone, GraphNode[]>;
  /** How many nodes each zone holds, for its tab. */
  counts: Record<Zone, number>;
}

/**
 * The deck list: the same nodes the canvas draws, bucketed by zone instead of
 * laid out left to right.
 *
 * It calls `deriveGraph` rather than re-reading the world, so the list and the
 * canvas cannot disagree about what is drawn or what state it is in. The lanes
 * are not read: a lane is a horizontal idea, and the narrow layout has no room
 * for one.
 *
 * Inside a zone the nodes needing the user come first, then the unanswered
 * ones. The canvas shows the whole board, so a glow or a border carries that;
 * a list cannot, so it sorts. The order is otherwise `deriveGraph`'s, which
 * is oldest first.
 */
export function deriveDeck(world: WorldView, opts: DeckOptions): Deck {
  const graph = deriveGraph(world, { filters: opts.filters });
  const zones: Record<Zone, GraphNode[]> = { done: [], running: [], notStarted: [] };
  for (const node of graph.nodes) zones[zoneForState(node.progress.state)].push(node);
  for (const zone of ZONES) {
    // A stable sort, so each band keeps `deriveGraph`'s order.
    zones[zone] = [...zones[zone]].sort((a, b) => callRank(a) - callRank(b));
  }
  return {
    zones,
    counts: {
      done: zones.done.length,
      running: zones.running.length,
      notStarted: zones.notStarted.length,
    },
  };
}

/** How strongly a node calls for the user: an ask, then an unread message, then the rest. */
function callRank(node: GraphNode): number {
  if (node.progress.state === 'needs-attention') return 0;
  return node.progress.state === 'unanswered' ? 1 : 2;
}

/** What a zone's tab is called. The words the canvas's zone strip uses. */
export function zoneLabel(zone: Zone): string {
  switch (zone) {
    case 'done':
      return 'Done';
    case 'running':
      return 'Running';
    case 'notStarted':
      return 'Not started';
  }
}

/** What a zone with nothing in it says, in its own words rather than "no results". */
export function emptyZoneWords(zone: Zone): string {
  switch (zone) {
    case 'done':
      return 'Nothing finished yet.';
    case 'running':
      return 'Nothing is running.';
    case 'notStarted':
      return 'Nothing is waiting to start.';
  }
}
