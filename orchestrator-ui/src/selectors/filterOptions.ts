import type { View } from '../store/uiSlice';
import type { Filters } from './filters';
import { branchKey, noFilters } from './filters';
import { deriveGraph } from './graph';
import type { WorldView } from './world';
import type { WorktreeFilters } from './worktrees';
import { listWorktrees } from './worktrees';

export interface BranchOption {
  key: string;
  label: string;
}

/** A project and a branch some view draws work for. The branch is `''` when there is none. */
interface Origin {
  project: string;
  branch: string;
}

/** What each view draws, as the projects and branches the filters can match. */
function originsOf(world: WorldView, view: View, worktreeFilters: WorktreeFilters): Origin[] {
  switch (view) {
    case 'canvas': {
      // The graph decides what the Desk draws, so the options cannot disagree
      // with it. The agent status filter does not narrow them.
      const graph = deriveGraph(world, { filters: noFilters() });
      const nodes = graph.nodes.map((node) => ({
        project: node.groupId,
        // A free agent's branch is its worktree's, as the branch filter reads it.
        branch: node.task ? node.task.branch : (node.worktree?.branch ?? ''),
      }));
      // A lane can hold only empty worktrees. A branch filter hides those, so
      // the lane gives its project and no branch.
      const lanes = graph.groups.map((group) => ({ project: group.id, branch: '' }));
      return [...nodes, ...lanes];
    }
    case 'list':
      return Object.values(world.tasks).map((t) => ({ project: t.project, branch: t.branch }));
    case 'worktrees':
      // The worktrees view has no Branch control.
      return listWorktrees(world, noFilters(), worktreeFilters).map((group) => ({
        project: group.project,
        branch: '',
      }));
    case 'comms':
      // A comm belongs to no project.
      return [];
  }
}

/**
 * The choices the filter bar offers: the projects and branches of the views on
 * screen. A selected value stays offered, so a view switch keeps the filter.
 * A selected branch drops out once no view in the world names it, so the bar
 * can clear it. A selected project has no such check: it stays until the user
 * changes it.
 */
export function filterOptions(
  world: WorldView,
  filters: Pick<Filters, 'project' | 'branch'>,
  views: readonly View[],
  worktreeFilters: WorktreeFilters,
): { projects: string[]; branches: BranchOption[] } {
  const origins = new Map<View, Origin[]>();
  const of = (view: View) => {
    let held = origins.get(view);
    if (!held) origins.set(view, (held = originsOf(world, view, worktreeFilters)));
    return held;
  };
  const keyOf = (o: Origin) => (o.branch ? branchKey(o.project, o.branch) : '');

  const shown = views.flatMap(of);
  const projects = new Set(shown.map((o) => o.project).filter(Boolean));
  if (filters.project) projects.add(filters.project);
  const branches = new Set(shown.map(keyOf).filter(Boolean));
  const kept = filters.branch;
  // Only the Desk and Tasks name branches.
  if (
    kept &&
    !branches.has(kept) &&
    (['canvas', 'list'] as const).flatMap(of).some((o) => keyOf(o) === kept)
  ) {
    branches.add(kept);
  }

  const prefix = filters.project ? `${filters.project}/` : null;
  const options = [...branches]
    .filter((key) => !prefix || key.startsWith(prefix))
    // The label carries the project only while several projects are listed.
    .map((key) => ({ key, label: prefix ? key.slice(prefix.length) : key }));
  return {
    projects: [...projects].sort(),
    branches: options.sort((a, b) => a.key.localeCompare(b.key)),
  };
}
