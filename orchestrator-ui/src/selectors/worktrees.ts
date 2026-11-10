import type { Agent, Worktree } from '../protocol/entities';
import { ownerOf } from '../protocol/ids';
import type { WorldView } from './world';

/** The worktree table's own filters. */
export interface WorktreeFilters {
  /** Whether a parked worktree is listed. Off by default: a closed worktree
   * holds no branch and no work, so it is noise until someone wants to reuse
   * or delete one. */
  showClosed: boolean;
}

export function noWorktreeFilters(): WorktreeFilters {
  return { showClosed: false };
}

/** One worktree and the work happening in it. */
export interface WorktreeRow {
  worktree: Worktree;
  /**
   * The agents running in this worktree. Not `sessionCount`, which is a
   * process sweep and counts a shell as readily as an agent: this is what the
   * world knows is working here.
   */
  agents: Agent[];
}

/**
 * The agents working in a worktree.
 *
 * A subagent runs in its parent's worktree and an exited row lingers in the
 * world, so neither counts as work happening here.
 */
export function trackedAgents(world: WorldView, worktreeId: string): Agent[] {
  return Object.values(world.agents).filter(
    (a) => a.worktreeId === worktreeId && !a.parent && a.state !== 'exited',
  );
}

/**
 * Whether this server owns the worktree. `ownPrefix` is its **Id prefix**: a
 * server with `host_name:` owns prefixed ids of its own. A bare id names no
 * other owner, so it is this server's.
 */
export function isOwnWorktree(worktree: Worktree, ownPrefix: string): boolean {
  const owner = ownerOf(worktree.id);
  return !owner || owner === ownPrefix;
}

/** The worktree's name as a reader sees it: `nato`, or `<owner>:<nato>` when another host owns it. */
export function worktreeName(worktree: Worktree, ownPrefix: string): string {
  return isOwnWorktree(worktree, ownPrefix)
    ? worktree.nato
    : `${ownerOf(worktree.id)}:${worktree.nato}`;
}

/**
 * The name a reader sees for a **Parent server**: the first DNS label of its
 * URL's host, the whole host for an IP address, or `''` for a URL that does
 * not parse.
 */
export function parentName(parentUrl: string): string {
  try {
    const { hostname } = new URL(parentUrl);
    return IP_HOST.test(hostname) ? hostname : (hostname.split('.')[0] ?? '');
  } catch {
    return '';
  }
}

/** An IPv4 address, or an IPv6 one in its URL brackets: no DNS label to take. */
const IP_HOST = /^(\d+\.){3}\d+$|^\[/;

/**
 * The **Parent server** whose **Delegated agent** works in a worktree this
 * server owns, or `''`. The newest such agent names it.
 */
export function usedByParent(world: WorldView, worktree: Worktree, ownPrefix: string): string {
  if (!isOwnWorktree(worktree, ownPrefix)) return '';
  const delegated = trackedAgents(world, worktree.id)
    .filter((a) => a.parentUrl)
    .sort((a, b) => b.startedAt.localeCompare(a.startedAt));
  return delegated.length ? parentName(delegated[0]!.parentUrl) : '';
}

/** Whether this is `_main`, which holds the project's main checkout and so never closes. */
export function isMain(worktree: Worktree): boolean {
  return worktree.nato === '_main';
}

/** Whether a close is on offer. A closed worktree has nothing left to close. */
export function canClose(worktree: Worktree): boolean {
  return !isMain(worktree) && !worktree.isClosed;
}

/** The branch as a label. A closed worktree carries none, and neither does a detached one. */
export function branchLabel(worktree: Worktree): string {
  return worktree.isClosed ? '(closed)' : worktree.branch || '(detached)';
}

/**
 * `_main` first, then the NATO names in order.
 *
 * `_main` leads because it holds the branch the others are cut from, and it is
 * the one worktree that never closes — so it is the row a reader orients by.
 */
export function byName(a: Worktree, b: Worktree): number {
  if (a.nato === '_main') return b.nato === '_main' ? 0 : -1;
  if (b.nato === '_main') return 1;
  return a.nato.localeCompare(b.nato);
}

/**
 * One project's worktrees that the filters allow, in the order the table draws them.
 *
 * Read from the world rather than from the desk, so a worktree nobody works in
 * still draws. `listEmptyWorktrees` in `graph.ts` reads the world the same way.
 */
export function projectWorktrees(
  world: WorldView,
  project: string,
  worktreeFilters: WorktreeFilters,
): WorktreeRow[] {
  return Object.values(world.worktrees)
    .filter((w) => w.project === project && (worktreeFilters.showClosed || !w.isClosed))
    .sort(byName)
    .map((worktree) => ({ worktree, agents: trackedAgents(world, worktree.id) }));
}
