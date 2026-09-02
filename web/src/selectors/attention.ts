import type { Attention, AttentionKind } from '../protocol/attention';
import { isOpen } from '../protocol/attention';
import type { World } from '../protocol/events';
import type { TaskId } from '../protocol/ids';

const RANK: Partial<Record<AttentionKind, number>> = { plan_review: 0, question: 1, permission: 2 };
const rank = (kind: AttentionKind) => RANK[kind] ?? 3;

/**
 * Open items: plan reviews, then questions, then permissions, then the rest;
 * oldest first within each. With `visible`, only items on those tasks, so the
 * chip agrees with a filtered canvas.
 */
export function openAttention(world: World, visible?: ReadonlySet<TaskId>): Attention[] {
  return Object.values(world.attention)
    .filter(isOpen)
    .filter((a) => !visible || (a.taskId !== null && visible.has(a.taskId)))
    .sort((a, b) => rank(a.kind) - rank(b.kind) || a.raisedAt.localeCompare(b.raisedAt));
}

/** The task the attention chip should take the user to next, cycling from `current`. */
export function nextAttentionTask(
  world: World,
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
