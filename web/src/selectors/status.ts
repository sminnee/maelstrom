import type { TaskRow } from '../api/types';
import type { Agent } from '../protocol/entities';

/** A document's status in words: `awaiting-review` becomes "awaiting review". */
export function describeDocumentStatus(status: string): string {
  return status.replace(/-/g, ' ');
}

/**
 * Whether `describeState` would only restate the task's status. True for the
 * three statuses it reads straight off the task, so a view that already shows
 * the status can drop the words rather than say the same thing twice.
 */
export function stateRestatesStatus(task: TaskRow | undefined, agent: Agent | undefined): boolean {
  if (!task) return false;
  if (task.status === 'done' || task.status === 'cancelled') return true;
  return task.status === 'blocked' && !agent;
}

/**
 * A task's state in words, for the node and the expanded node. Raw agent
 * states such as `awaiting-question` never reach the screen.
 */
export function describeState(task: TaskRow | undefined, agent: Agent | undefined): string {
  if (task?.status === 'done') return 'Done';
  if (task?.status === 'cancelled') return 'Cancelled';
  if (agent?.state === 'exited') {
    // An unobserved exit code is not a clean exit; the node draws it red too.
    if (agent.exitCode === 0) return 'Exited';
    return agent.exitCode === null ? 'Exited (unknown code)' : `Exited (code ${agent.exitCode})`;
  }
  if (!agent) {
    if (task?.status === 'blocked') return 'Blocked';
    return task?.actionable ? 'Ready to launch' : 'Queued';
  }
  switch (agent.state) {
    case 'processing':
      return 'Working';
    case 'idle':
      return 'Idle';
    case 'awaiting-question':
      return 'Needs you · question';
    case 'awaiting-permission':
      return 'Needs you · permission';
    case 'awaiting-plan-review':
      return 'Needs you · plan review';
  }
}
