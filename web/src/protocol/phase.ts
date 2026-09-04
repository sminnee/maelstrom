import type { Attention } from './attention';
import { isOpen } from './attention';
import type { Agent, Phase, Task } from './entities';
import type { TaskId } from './ids';

/** The commands `phaseForCommand` knows. An unlisted one still runs; it has no phase. */
export const KNOWN_COMMANDS = ['shape', 'plan-task', 'plan-next-step', 'watch-pr'] as const;

/**
 * The phase a task's `command` puts it in. The phase is not sent on the wire:
 * it is a reading of `command`, and this is the one place that reading happens.
 */
export function phaseForCommand(command: string): Phase | null {
  switch (command) {
    case 'shape':
      return 'shape';
    case 'plan-task':
    case 'plan-next-step':
      return 'plan';
    case 'watch-pr':
      return 'land';
    // An execute task runs no skill, so no command is the ordinary build case.
    case '':
      return 'build';
    // A command nobody recognises is not a build task: it is a task whose phase
    // is unknown. The node draws no phase rather than claiming a wrong one.
    default:
      return null;
  }
}

/**
 * A phase as the operator reads it. Imperative, because a phase is work to do
 * — which is what keeps it from reading as an agent state ("Working", "Idle")
 * on a node that shows both.
 */
export function phaseLabel(phase: Phase): string {
  return phase.toUpperCase();
}

/** May maelstrom launch this task now. Same rule as the notebook. */
export function isActionable(task: Task, tasks: Record<TaskId, Task>): boolean {
  if (['done', 'cancelled', 'blocked', 'template'].includes(task.status)) return false;
  return task.follows.every((id) => tasks[id]?.status === 'done');
}

/** How a node draws: the state axis, orthogonal to phase. */
export type NodeState =
  'queued' | 'ready' | 'working' | 'needs-attention' | 'idle' | 'done' | 'cancelled' | 'exited';

export function nodeState(
  task: Task | undefined,
  agent: Agent | undefined,
  attention: readonly Attention[],
): NodeState {
  if (task?.status === 'done') return 'done';
  // Cancelled is terminal but not a success, so it never draws as one.
  if (task?.status === 'cancelled') return 'cancelled';
  // A dead agent is the stronger signal: its attention item still counts in
  // the chip, but the node draws red rather than orange. An unknown exit code
  // counts as abnormal: only an observed 0 is a clean exit.
  if (agent?.state === 'exited' && agent.exitCode !== 0) return 'exited';
  const open = attention.some(
    (item) =>
      isOpen(item) && ((task && item.taskId === task.id) || (agent && item.agentId === agent.id)),
  );
  if (open) return 'needs-attention';
  // Ready is the one waiting state the operator can act on, so it is the one
  // that gets a hue: queued is waiting on other work, ready is waiting on them.
  if (!agent) return task?.actionable ? 'ready' : 'queued';
  if (agent.state === 'exited') return 'idle';
  if (agent.state === 'processing') return 'working';
  return 'idle';
}
