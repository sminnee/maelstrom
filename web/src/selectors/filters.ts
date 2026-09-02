import type { World } from '../protocol/events';

export type GroupBy = 'project' | 'branch';

export interface Filters {
  project: string | null;
  branch: string | null;
  hideDone: boolean;
}

export function noFilters(): Filters {
  return { project: null, branch: null, hideDone: false };
}

/** The choices the filter bar offers, from what the world holds. */
export function filterOptions(world: World, filters: Filters) {
  const projects = Object.keys(world.projects).sort();
  const branches = new Set<string>();
  for (const task of Object.values(world.tasks)) {
    if (filters.project && task.project !== filters.project) continue;
    if (task.branch) branches.add(task.branch);
  }
  return { projects, branches: [...branches].sort() };
}
