import type { TaskId } from '../protocol/ids';

/** The two ends of a drag. React Flow leaves either null until the drag lands. */
export interface ConnectionEnds {
  source: string | null;
  target: string | null;
}

/**
 * Whether a drag between these two may be written.
 *
 * The same rules the server applies, checked here so the wire never lands
 * rather than landing and reverting on the next read. A task only ever follows
 * a task beside it in its own notebook, and a self-edge would strand the task:
 * nothing it follows could ever be done.
 *
 * A free agent draws on the board but is no task, so its bare id has no
 * project and no `follows` to write.
 */
export function canConnect({ source, target }: ConnectionEnds): boolean {
  if (!source || !target || source === target) return false;
  const from = projectOf(source);
  const to = projectOf(target);
  return from !== '' && from === to;
}

/** The project half of a qualified task id, or `''` when it carries none. */
function projectOf(id: string): string {
  const [project, notebookId] = id.split('/');
  return notebookId ? (project ?? '') : '';
}

/**
 * What the target follows once the wire is drawn. The write replaces the whole
 * list, so it carries every id the task already had. Dragging a wire that is
 * already there writes nothing new.
 */
export function followsAfterConnect(follows: readonly TaskId[], followed: TaskId): TaskId[] {
  return follows.includes(followed) ? [...follows] : [...follows, followed];
}

/**
 * What the target follows once the wire is cut. An empty list is a real edit:
 * it is how the last wire comes off.
 */
export function followsAfterDisconnect(follows: readonly TaskId[], followed: TaskId): TaskId[] {
  return follows.filter((id) => id !== followed);
}
