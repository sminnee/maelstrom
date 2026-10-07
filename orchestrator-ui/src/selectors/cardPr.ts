import type { TaskRow } from '../api/types';
import type { PrState, Worktree } from '../protocol/entities';
import type { GraphNode } from './graph';

/** A task's **Registered PR**: what put a PR on its card. */
export interface RegisteredPr {
  number: number;
  url: string;
}

/**
 * The PR a chip draws. `state` is `''` when the PR is not the one the
 * worktree reads, so the chip draws the number and link with no state.
 */
export interface PrReading {
  number: number;
  url: string;
  state: PrState | '';
  draft: boolean;
}

/** The node fields the choice reads. */
export type CardPrFacts = Pick<GraphNode, 'registeredPr' | 'worktree'>;

/** The chain a task belongs to: its parent, or itself when it roots one. */
function chainKey(task: TaskRow): string {
  return `${task.project}/${task.parent || task.notebookId}`;
}

/**
 * The latest Registered PR of each chain, by chain key. "Latest" is the
 * highest number: GitHub numbers a repo's PRs in the order they open.
 */
export function chainRegistrations(tasks: Iterable<TaskRow>): Map<string, RegisteredPr> {
  const byChain = new Map<string, RegisteredPr>();
  for (const task of tasks) {
    if (!task.prNumber) continue;
    const key = chainKey(task);
    const held = byChain.get(key);
    if (!held || task.prNumber > held.number) {
      byChain.set(key, { number: task.prNumber, url: task.prUrl });
    }
  }
  return byChain;
}

/**
 * The PR registered for `task`: its own, else the latest of its chain, read
 * from `chainRegistrations`. One PR per parent, so a later task in a chain
 * shows the PR an earlier one made.
 * See docs/dev/orchestrator-ui.md, "Which PR a task shows".
 */
export function registeredPr(
  task: TaskRow,
  chains: Map<string, RegisteredPr>,
): RegisteredPr | undefined {
  if (task.prNumber) return { number: task.prNumber, url: task.prUrl };
  return chains.get(chainKey(task));
}

/**
 * The PR a card shows, or `undefined` for none. Only a registration puts one
 * there, and `deriveGraph` gives a free agent none. Its state comes from the
 * worktree when the worktree's PR is the registered one.
 *
 * `worktree` defaults to the node's. The expanded card passes the one it
 * draws, which falls back to the agent's.
 */
export function cardPr(node: CardPrFacts, worktree = node.worktree): PrReading | undefined {
  const pr = node.registeredPr;
  if (!pr) return undefined;
  if (!worktree || !samePr(pr, worktree)) return { ...pr, state: '', draft: false };
  return {
    number: pr.number,
    url: pr.url || worktree.prUrl,
    state: worktree.prState,
    draft: worktree.prDraft,
  };
}

/** Whether `worktree` reads the registered PR: same number, and same repo when both URLs say. */
function samePr(pr: RegisteredPr, worktree: Worktree): boolean {
  if (worktree.prNumber !== pr.number) return false;
  return !pr.url || !worktree.prUrl || pr.url === worktree.prUrl;
}

/** A worktree's own PR, read the way a chip draws it. The branch view's PR. */
export function worktreePr(worktree: Worktree | undefined): PrReading | undefined {
  if (!worktree?.prNumber) return undefined;
  return {
    number: worktree.prNumber,
    url: worktree.prUrl,
    state: worktree.prState,
    draft: worktree.prDraft,
  };
}
