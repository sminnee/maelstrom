import type { Attention, AttentionKind } from '../protocol/attention';
import { isOpen } from '../protocol/attention';
import type { WorldView } from './world';
import type { TaskId } from '../protocol/ids';

const RANK: Partial<Record<AttentionKind, number>> = {
  plan_review: 0,
  document_review: 1,
  question: 2,
  permission: 3,
};
const rank = (kind: AttentionKind) => RANK[kind] ?? 4;

/**
 * Open items: plan reviews, then document reviews, then questions, then
 * permissions, then the rest; oldest first within each. With `visible`, only
 * items on those tasks, so the chip agrees with a filtered canvas.
 */
export function openAttention(world: WorldView, visible?: ReadonlySet<TaskId>): Attention[] {
  return Object.values(world.attention)
    .filter(isOpen)
    .filter((a) => !visible || (a.taskId !== null && visible.has(a.taskId)))
    .sort((a, b) => rank(a.kind) - rank(b.kind) || a.raisedAt.localeCompare(b.raisedAt));
}

/** The task the attention chip should take the user to next, cycling from `current`. */
export function nextAttentionTask(
  world: WorldView,
  current: TaskId | null,
  visible?: ReadonlySet<TaskId>,
): TaskId | null {
  const tasks = openAttention(world, visible)
    .map((a) => a.taskId)
    .filter((t): t is TaskId => !!t);
  const distinct = [...new Set(tasks)];
  if (distinct.length === 0) return null;
  const index = current ? distinct.indexOf(current) : -1;
  return distinct[(index + 1) % distinct.length] ?? null;
}
