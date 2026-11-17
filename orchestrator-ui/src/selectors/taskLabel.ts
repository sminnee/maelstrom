import type { Task } from '../protocol/entities';

/** The task fields a label reads. */
export type Labelled = Pick<Task, 'id' | 'codename' | 'notebookId' | 'project'>;

/**
 * The name the UI shows for a task: its **Codename** while it holds one, else
 * its bare notebook id. See CONTEXT.md, "Codename" and "Failover id".
 */
export function taskLabel(task: Labelled): string {
  return task.codename || task.notebookId;
}

/**
 * `taskLabel` with the project in front, for a place no lane names the
 * project. Without a codename this is the wire id, which already names it.
 */
export function qualifiedTaskLabel(task: Labelled): string {
  return task.codename ? `${task.project}/${task.codename}` : task.id;
}

/**
 * The tooltip for a shown label. A codename names the id beside it, so the
 * user can still use the id in a CLI command.
 */
export function taskLabelTitle(task: Labelled): string {
  return task.codename ? `${task.codename} · ${task.notebookId}` : task.notebookId;
}
