import type { NewWorkSeed } from '../store/uiSlice';
/**
 * Which agents the Desk shows. `planned` is a task that has not launched one.
 * `has-pr` is a task with a Registered PR: the cards that draw a PR chip.
 */
export type AgentStatusFilter =
  'all' | 'working' | 'idle' | 'working-idle' | 'terminated' | 'planned' | 'has-pr';

/** What the Agent status dropdown and the agents chip call each status. */
export const AGENT_STATUS_LABELS: Record<AgentStatusFilter, string> = {
  all: 'All',
  working: 'Working',
  idle: 'Idle',
  'working-idle': 'Working + Idle',
  terminated: 'Terminated',
  planned: 'Planned',
  'has-pr': 'Has PR',
};

export interface Filters {
  project: string | null;
  /** A branch key, `<project>/<branch>`: two projects may share a branch name. */
  branch: string | null;
  agentStatus?: AgentStatusFilter;
  /** The Search text. The Desk and Tasks share it. */
  text: string;
  /** A comm id: only the tasks linked to it. The Desk and Tasks share it. */
  comm: string | null;
}

export const branchKey = (project: string, branch: string) => `${project}/${branch}`;

/**
 * The new-work seed the filters in force give, or `null` when they name no project, branch or
 * comm. A branch key splits at its first `/`: a project name has none, a branch name can.
 */
export function filterSeed(filters: Filters): Extract<NewWorkSeed, { kind: 'filters' }> | null {
  if (!filters.project && !filters.branch && !filters.comm) return null;
  const [branchProject = '', ...rest] = filters.branch?.split('/') ?? [];
  const branch = rest.join('/');
  return {
    kind: 'filters',
    project: filters.project ?? branchProject,
    branch,
    comms: filters.comm ? [filters.comm] : [],
  };
}

export function noFilters(): Filters {
  return { project: null, branch: null, agentStatus: 'all', text: '', comm: null };
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
