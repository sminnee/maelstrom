import type { Agent, Worktree } from '../protocol/entities';
import type { Filters } from './filters';
import type { WorldView } from './world';

/** The Worktrees view's own filters, beside the shared project one. */
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

/** Every worktree of one project, in the order the table draws them. */
export interface WorktreeGroup {
  project: string;
  rows: WorktreeRow[];
}

/**
 * The agents working in a worktree.
 *
 * A subagent runs in its parent's worktree and an exited row lingers in the
 * world, so neither counts as work happening here.
 */
function trackedAgents(world: WorldView, worktreeId: string): Agent[] {
  return Object.values(world.agents).filter(
    (a) => a.worktreeId === worktreeId && !a.parent && a.state !== 'exited',
  );
}

/**
 * `_main` first, then the NATO names in order.
 *
 * `_main` leads because it holds the branch the others are cut from, and it is
 * the one worktree that never closes — so it is the row a reader orients by.
 */
function byName(a: Worktree, b: Worktree): number {
  if (a.nato === '_main') return b.nato === '_main' ? 0 : -1;
  if (b.nato === '_main') return 1;
  return a.nato.localeCompare(b.nato);
}

/**
 * Every worktree the filters allow, grouped by project.
 *
 * Read from the world rather than from what is on the desk, so a worktree
 * nobody is working in still draws — which is the whole reason this view
 * exists. `seedWorktreeLanes` in `graph.ts` reads the world the same way, for
 * the same reason.
 */
export function listWorktrees(
  world: WorldView,
  filters: Pick<Filters, 'project'>,
  worktreeFilters: WorktreeFilters,
): WorktreeGroup[] {
  const byProject = new Map<string, Worktree[]>();
  for (const worktree of Object.values(world.worktrees)) {
    if (worktree.isClosed && !worktreeFilters.showClosed) continue;
    if (filters.project && worktree.project !== filters.project) continue;
    const held = byProject.get(worktree.project);
    if (held) held.push(worktree);
    else byProject.set(worktree.project, [worktree]);
  }
  return [...byProject.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([project, worktrees]) => ({
      project,
      rows: worktrees.sort(byName).map((worktree) => ({
        worktree,
        agents: trackedAgents(world, worktree.id),
      })),
    }));
}
