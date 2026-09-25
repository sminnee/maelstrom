import type { TaskRow } from '../api/types';
import type { TaskId } from '../protocol/ids';

/**
 * Every task `id` follows (`before`) and every task that follows it (`after`),
 * direct and indirect, nearest first. One depth is in natural id order, so the order
 * of `tasks` decides nothing. An id with no task is skipped: there is nothing
 * to show or put on the desk.
 */
export function followsReach(
  tasks: Record<TaskId, TaskRow>,
  id: TaskId,
): { before: TaskRow[]; after: TaskRow[] } {
  const followers: Record<TaskId, TaskId[]> = {};
  for (const t of Object.values(tasks)) {
    for (const f of t.follows) (followers[f] ??= []).push(t.id);
  }
  return {
    before: reach(tasks, id, (t) => tasks[t]?.follows ?? []),
    after: reach(tasks, id, (t) => followers[t] ?? []),
  };
}

/** Breadth-first from `id` along `next`; the visited set ends cycles and diamonds. */
function reach(
  tasks: Record<TaskId, TaskRow>,
  id: TaskId,
  next: (id: TaskId) => TaskId[],
): TaskRow[] {
  const seen = new Set([id]);
  const found: TaskRow[] = [];
  let depth = [id];
  while (depth.length > 0) {
    const nextDepth = [...new Set(depth.flatMap(next))]
      .filter((n) => !seen.has(n) && n in tasks)
      .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    for (const n of nextDepth) {
      seen.add(n);
      found.push(tasks[n]!);
    }
    depth = nextDepth;
  }
  return found;
}
