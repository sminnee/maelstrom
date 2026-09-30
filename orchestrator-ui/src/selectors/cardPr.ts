import type { Worktree } from '../protocol/entities';
import { zoneForState } from '../protocol/progress';
import type { GraphNode } from './graph';

/** The node fields the choice reads. */
export type CardPrFacts = Pick<GraphNode, 'kind' | 'task' | 'agent' | 'worktree' | 'progress'>;

/**
 * The worktree whose PR a card shows, or `undefined` for none. A task card not
 * started shows none. A merged PR shows only if it merged at or after the task
 * started, else the card's agent started. A free agent's card, an open PR, and
 * a task with no start time keep the PR. See docs/dev/orchestrator-ui.md,
 * "Which PR a task shows".
 *
 * `worktree` defaults to the node's. The expanded card passes the one it
 * draws, which falls back to the agent's.
 */
export function cardPr(node: CardPrFacts, worktree = node.worktree): Worktree | undefined {
  if (!worktree) return undefined;
  if (node.kind !== 'task') return worktree;
  if (zoneForState(node.progress.state) === 'notStarted') return undefined;
  if (worktree.prState !== 'merged') return worktree;
  const merged = Date.parse(worktree.prMergedAt);
  const started = Date.parse(node.task?.startedAt || node.agent?.startedAt || '');
  // An unknown time cannot rule the PR out.
  if (Number.isNaN(merged) || Number.isNaN(started)) return worktree;
  return merged >= started ? worktree : undefined;
}
