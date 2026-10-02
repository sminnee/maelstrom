import type { WorldView } from './world';

/** Which agents the Desk shows. `planned` is a task that has not launched one. */
export type AgentStatusFilter =
  'all' | 'working' | 'idle' | 'working-idle' | 'terminated' | 'planned';

export interface Filters {
  project: string | null;
  /** A branch key, `<project>/<branch>`: two projects may share a branch name. */
  branch: string | null;
  agentStatus?: AgentStatusFilter;
}

export const branchKey = (project: string, branch: string) => `${project}/${branch}`;

export function noFilters(): Filters {
  return { project: null, branch: null, agentStatus: 'all' };
}

/**
 * Whether a deck row or card names its node's project. The deck has no lane
 * to name it, so it does while the project filter names none.
 */
export const showsProject = (filters: Filters) => !filters.project;

export interface BranchOption {
  key: string;
  label: string;
}

/** The choices the filter bar offers, from what the world holds. */
export function filterOptions(
  world: WorldView,
  filters: Filters,
): { projects: string[]; branches: BranchOption[] } {
  const projects = Object.keys(world.projects).sort();
  const branches = new Map<string, BranchOption>();
  for (const task of Object.values(world.tasks)) {
    if (filters.project && task.project !== filters.project) continue;
    if (!task.branch) continue;
    const key = branchKey(task.project, task.branch);
    // The label carries the project only while several projects are listed.
    const label = filters.project ? task.branch : key;
    branches.set(key, { key, label });
  }
  return { projects, branches: [...branches.values()].sort((a, b) => a.key.localeCompare(b.key)) };
}
