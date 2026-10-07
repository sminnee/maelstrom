/** Which agents the Desk shows. `planned` is a task that has not launched one. */
export type AgentStatusFilter =
  'all' | 'working' | 'idle' | 'working-idle' | 'terminated' | 'planned';

/** What the Agent status dropdown and the agents chip call each status. */
export const AGENT_STATUS_LABELS: Record<AgentStatusFilter, string> = {
  all: 'All',
  working: 'Working',
  idle: 'Idle',
  'working-idle': 'Working + Idle',
  terminated: 'Terminated',
  planned: 'Planned',
};

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
