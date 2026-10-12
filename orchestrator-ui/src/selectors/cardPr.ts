import type { TaskRow } from '../api/types';
import type { PrState, Worktree } from '../protocol/entities';
import type { GraphNode } from './graph';

/**
 * The PR a chip draws. `state` is `''` when the PR is not the one the
 * worktree reads, so the chip draws the number and link with no state.
 * `withdrawn` is the task's **Withdrawn PR**: the agent is working again. A
 * merged PR is never withdrawn.
 */
export interface PrReading {
  number: number;
  url: string;
  state: PrState | '';
  draft: boolean;
  withdrawn: boolean;
}

/** The node fields the choice reads. */
export type CardPrFacts = Pick<GraphNode, 'task' | 'worktree'>;

/** Whether a task has a **Registered PR**: whether its card draws a PR chip. */
export const hasRegisteredPr = (task: TaskRow | undefined): task is TaskRow => !!task?.prNumber;

/**
 * The PR a card shows: the **Registered PR** of its own task, never its
 * chain's or its branch's. A free agent has no task, so none. Its state comes
 * from the worktree when the worktree's PR is the registered one.
 * See docs/dev/orchestrator-ui.md, "Which PR a task shows".
 *
 * `worktree` defaults to the node's. The expanded card passes the one it
 * draws, which falls back to the agent's.
 */
export function cardPr(node: CardPrFacts, worktree = node.worktree): PrReading | undefined {
  const task = node.task;
  if (!hasRegisteredPr(task)) return undefined;
  const pr = { number: task.prNumber, url: task.prUrl };
  const withdrawn = task.prWithdrawn;
  if (!worktree || !samePr(pr, worktree)) return { ...pr, state: '', draft: false, withdrawn };
  return {
    number: pr.number,
    url: pr.url || worktree.prUrl,
    state: worktree.prState,
    draft: worktree.prDraft,
    // A merge lands the work, and no registration follows one to clear the flag.
    withdrawn: withdrawn && worktree.prState !== 'merged',
  };
}

/** Whether `worktree` reads the registered PR: same number, and same repo when both URLs say. */
function samePr(pr: { number: number; url: string }, worktree: Worktree): boolean {
  if (worktree.prNumber !== pr.number) return false;
  return !pr.url || !worktree.prUrl || pr.url === worktree.prUrl;
}

/**
 * A worktree's own PR, read the way a chip draws it. The branch view's PR.
 * Never withdrawn: a withdrawal is the task's, not the branch's.
 */
export function worktreePr(worktree: Worktree | undefined): PrReading | undefined {
  if (!worktree?.prNumber) return undefined;
  return {
    number: worktree.prNumber,
    url: worktree.prUrl,
    state: worktree.prState,
    draft: worktree.prDraft,
    withdrawn: false,
  };
}
