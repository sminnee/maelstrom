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
  /** The Search text. The Desk and Tasks share it. */
  text: string;
}

export const branchKey = (project: string, branch: string) => `${project}/${branch}`;

export function noFilters(): Filters {
  return { project: null, branch: null, agentStatus: 'all', text: '' };
}

/**
 * Whether a deck row or card names its node's project. The deck has no lane
 * to name it, so it does while the project filter names none.
 */
export const showsProject = (filters: Filters) => !filters.project;

/** The Search text as it matches: trimmed, lower case. Empty means no search. */
const needleOf = (filters: Filters) => filters.text.trim().toLowerCase();

/** Whether a Search is in force. */
export const searching = (filters: Filters) => needleOf(filters) !== '';

/** Whether one of `fields` holds the Search text, whatever its case. No search matches all. */
export function matchesText(fields: string[], filters: Filters): boolean {
  const needle = needleOf(filters);
  return !needle || fields.some((field) => field.toLowerCase().includes(needle));
}
