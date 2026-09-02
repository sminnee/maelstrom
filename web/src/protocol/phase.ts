import type { Attention } from './attention';
import { isOpen } from './attention';
import type { Agent, Phase, Task } from './entities';
import type { TaskId } from './ids';

/** The phase a task's `command` puts it in. The real backend applies this rule. */
export function phaseForCommand(command: string): Phase {
  switch (command) {
    case 'shape':
      return 'shaping';
    case 'plan-task':
    case 'plan-next-step':
      return 'planning';
    case 'watch-pr':
      return 'finalising';
    default:
      return 'executing';
  }
}

/** May maelstrom launch this task now. Same rule as the notebook. */
export function isActionable(task: Task, tasks: Record<TaskId, Task>): boolean {
  if (['done', 'cancelled', 'blocked', 'template'].includes(task.status)) return false;
  return task.follows.every((id) => tasks[id]?.status === 'done');
}

/** How a node draws: the state axis, orthogonal to phase. */
export type NodeState = 'queued' | 'working' | 'needs-attention' | 'idle' | 'done' | 'exited';

export function nodeState(
  task: Task,
  agent: Agent | undefined,
  attention: readonly Attention[],
): NodeState {
  if (task.status === 'done' || task.status === 'cancelled') return 'done';
  // A dead agent is the stronger signal: its attention item still counts in
  // the chip, but the node draws red rather than orange. An unknown exit code
  // counts as abnormal: only an observed 0 is a clean exit.
  if (agent?.state === 'exited' && agent.exitCode !== 0) return 'exited';
  const open = attention.some(
    (item) => isOpen(item) && (item.taskId === task.id || (agent && item.agentId === agent.id)),
  );
  if (open) return 'needs-attention';
  if (!agent) return 'queued';
  if (agent.state === 'exited') return 'idle';
  if (agent.state === 'processing') return 'working';
  return 'idle';
}
