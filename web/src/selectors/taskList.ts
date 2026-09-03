import type { Agent, Task, TaskStatus } from '../protocol/entities';
import type { World } from '../protocol/events';
import type { TaskId } from '../protocol/ids';
import type { Filters } from './filters';
import { branchKey, noFilters } from './filters';

/** The six statuses, in the order the task list shows them. */
export const LIST_STATUSES: readonly TaskStatus[] = [
  'todo',
  'in-progress',
  'blocked',
  'done',
  'cancelled',
  'template',
];

/** What the task list narrows by: the canvas's filters, plus status and text. */
export interface ListFilters extends Filters {
  /** Empty means every status. */
  statuses: TaskStatus[];
  text: string;
}

export function noListFilters(): ListFilters {
  return { ...noFilters(), statuses: [], text: '' };
}

/** One row of the task list. */
export interface ListRow {
  task: Task;
  onDesk: boolean;
  agent?: Agent;
}

/** The rows the task list shows, sorted by project then id. */
export function listTasks(world: World, filters: ListFilters): ListRow[] {
  const text = filters.text.trim().toLowerCase();
  // Built once, not per row: the task list exists for the scale that broke
  // the canvas, and a scan per row would be O(tasks x agents) over ~700 tasks.
  const agents = agentsByTask(world);
  return Object.values(world.tasks)
    .filter((t) => !filters.project || t.project === filters.project)
    .filter((t) => !filters.branch || branchKey(t.project, t.branch) === filters.branch)
    .filter((t) => filters.statuses.length === 0 || filters.statuses.includes(t.status))
    .filter((t) => !text || matches(t, text))
    .sort((a, b) => a.project.localeCompare(b.project) || a.id.localeCompare(b.id))
    .map((task) => ({
      task,
      onDesk: task.id in world.desk,
      agent: agents.get(task.id),
    }));
}

/**
 * One agent per task: the live one, else the last that ran.
 *
 * The same choice `agentForTask` makes, made once for every task at a time.
 */
function agentsByTask(world: World): Map<TaskId, Agent> {
  const live = new Map<TaskId, Agent>();
  const last = new Map<TaskId, Agent>();
  for (const agent of Object.values(world.agents)) {
    if (!agent.taskId) continue;
    last.set(agent.taskId, agent);
    if (agent.state !== 'exited' && !live.has(agent.taskId)) live.set(agent.taskId, agent);
  }
  return new Map([...last, ...live]);
}

function matches(task: Task, text: string): boolean {
  return [task.id, task.notebookId, task.title].some((field) => field.toLowerCase().includes(text));
}
