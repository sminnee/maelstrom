import { deskIdForTask } from '../protocol/deskId';
import type { TaskRow } from '../api/types';
import type { Agent, TaskStatus } from '../protocol/entities';
import type { WorldView } from './world';
import type { Filters } from './filters';
import { branchKey, matchesText } from './filters';
import { agentsByTask } from './graph';

/** The statuses the task list opens on: work that is still live. */
export const LIVE_STATUSES: readonly TaskStatus[] = ['todo', 'in-progress', 'blocked'];

/** The filters that apply only to Tasks. */
export interface ListFilters {
  /** Empty means every status. */
  statuses: TaskStatus[];
}

export function noListFilters(): ListFilters {
  return { statuses: [...LIVE_STATUSES] };
}

/** One row of the task list. */
export interface ListRow {
  task: TaskRow;
  onDesk: boolean;
  agent?: Agent;
}

/** The rows the task list shows: by project, then oldest first, then id. */
export function listTasks(world: WorldView, filters: Filters, listFilters: ListFilters): ListRow[] {
  // Built once, not per row: the task list exists for the scale that broke
  // the canvas, and a scan per row would be O(tasks x agents) over ~700 tasks.
  const agents = agentsByTask(world);
  return Object.values(world.tasks)
    .filter((t) => !filters.project || t.project === filters.project)
    .filter((t) => !filters.branch || branchKey(t.project, t.branch) === filters.branch)
    .filter((t) => !filters.comm || t.comms.includes(filters.comm))
    .filter((t) => listFilters.statuses.length === 0 || listFilters.statuses.includes(t.status))
    .filter((t) => matchesText([t.id, t.notebookId, t.title], filters))
    .sort(
      (a, b) =>
        a.project.localeCompare(b.project) ||
        a.created.localeCompare(b.created) ||
        a.id.localeCompare(b.id),
    )
    .map((task) => ({
      task,
      onDesk: deskIdForTask(task.id) in world.desk,
      agent: agents.get(task.id),
    }));
}
