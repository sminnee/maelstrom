import type { TaskStatus } from '../protocol/entities';
import type { Zone } from '../protocol/progress';
import { isActionable } from '../protocol/phase';
import { ZONES, progressOf, zoneForState } from '../protocol/progress';
import type { WorldView } from './world';
import type { Filters } from './filters';
import { deriveGraph, nodeZone, type GraphNode } from './graph';

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
  const zones: Record<Zone, GraphNode[]> = { queued: [], todo: [], running: [], done: [] };
  for (const node of graph.nodes) zones[nodeZone(node)].push(node);
  for (const zone of ZONES) {
    // A stable sort, so each band keeps `deriveGraph`'s order.
    zones[zone] = [...zones[zone]].sort((a, b) => callRank(a) - callRank(b));
  }
  return {
    zones,
    counts: {
      queued: zones.queued.length,
      todo: zones.todo.length,
      running: zones.running.length,
      done: zones.done.length,
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
    case 'queued':
      return 'Queued';
    case 'todo':
      return 'To do';
  }
}

/** What a zone with nothing in it says, in its own words rather than "no results". */
export function emptyZoneWords(zone: Zone): string {
  switch (zone) {
    case 'done':
      return 'Nothing finished yet.';
    case 'running':
      return 'Nothing is running.';
    case 'queued':
      return 'Nothing is waiting on other work.';
    case 'todo':
      return 'Nothing is ready to start.';
  }
}

/** The status a drop on each zone sets. Running has none: a drop never launches. */
const DROP_STATUS: Record<Zone, TaskStatus | null> = {
  queued: 'blocked',
  todo: 'todo',
  running: null,
  done: 'done',
};

/**
 * The status a drop of `node` on `zone` sets, or null when the drop is refused.
 *
 * A drop is legal when its status differs from the current one and puts the
 * node in `zone`. The zone is predicted with `progressOf`, the reading the
 * board draws from, so a card always lands in the column it was dropped on.
 * `actionable` is predicted with `isActionable`, the server's rule.
 *
 * Only a task drags. A free agent has no status, and an opening node or one an
 * operation holds has no settled zone. A template never draws.
 */
export function dropStatus(
  node: GraphNode,
  zone: Zone,
  tasks: WorldView['tasks'],
): TaskStatus | null {
  const { task } = node;
  if (node.kind !== 'task' || !task || node.zone || nodeZone(node) === zone) return null;
  const status = DROP_STATUS[zone];
  if (!status || status === task.status) return null;
  const actionable = isActionable({ ...task, status }, tasks);
  const predicted = progressOf(
    { ...task, status, actionable },
    node.agent,
    node.attention,
    node.worktree?.gate ?? null,
  );
  return zoneForState(predicted.state) === zone ? status : null;
}
