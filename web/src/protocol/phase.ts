import type { Zone } from '../canvas/columns';
import type { Attention } from './attention';
import { isOpen } from './attention';
import type { Agent, Phase, Task } from './entities';
import type { TaskId } from './ids';

/**
 * The phase each command puts a task in. An `impeccable` job that hands back a
 * plan or a review is shape; one that changes the UI is build. A command that
 * sets a session or a document up, rather than moving a task through a phase,
 * is not a key.
 *
 * The `impeccable` keys mirror that skill's own Commands table, which lives
 * outside this repo -- nothing here tells you when the skill has moved on.
 */
const PHASES = {
  shape: 'shape',
  'plan-task': 'plan',
  'plan-next-step': 'plan',
  'watch-pr': 'land',
  'impeccable shape': 'shape',
  'impeccable critique': 'shape',
  'impeccable audit': 'shape',
  'impeccable extract': 'build',
  'impeccable polish': 'build',
  'impeccable bolder': 'build',
  'impeccable quieter': 'build',
  'impeccable distill': 'build',
  'impeccable harden': 'build',
  'impeccable onboard': 'build',
  'impeccable animate': 'build',
  'impeccable colorize': 'build',
  'impeccable typeset': 'build',
  'impeccable layout': 'build',
  'impeccable delight': 'build',
  'impeccable overdrive': 'build',
  'impeccable clarify': 'build',
  'impeccable adapt': 'build',
  'impeccable optimize': 'build',
} as const satisfies Record<string, Phase>;

/**
 * The commands the editor suggests: a shortlist, not every key of `PHASES`.
 * The field is free text, so any command may be typed in full, and one that
 * `PHASES` holds still draws its phase.
 */
export const KNOWN_COMMANDS = [
  'shape',
  'plan-task',
  'plan-next-step',
  'watch-pr',
  'impeccable shape',
  'impeccable critique',
  'impeccable audit',
  'impeccable layout',
] as const satisfies readonly (keyof typeof PHASES)[];

/**
 * The phase a task's `command` puts it in. The phase is not sent on the wire:
 * it is a reading of `command`, and this is the one place that reading happens.
 */
export function phaseForCommand(command: string): Phase | null {
  // An execute task runs no skill, so no command is the ordinary build case.
  if (command === '') return 'build';
  return (PHASES as Record<string, Phase>)[command] ?? null;
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
  task: Pick<Task, 'id' | 'status' | 'actionable'> | undefined,
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

/**
 * Which of the three progress zones a state falls in. The rule underneath: the
 * running zone means an agent has been launched and the task is not finished.
 * Cancelled is history, so it sits with done.
 */
export function zoneForState(state: NodeState): Zone {
  switch (state) {
    case 'done':
    case 'cancelled':
      return 'done';
    // `exited` sits here rather than in done: a run that stopped without the
    // task being marked done is not history, it is unfinished work that needs
    // the operator, and the done zone is for work that is actually settled.
    case 'working':
    case 'needs-attention':
    case 'idle':
    case 'exited':
      return 'running';
    case 'ready':
    case 'queued':
      return 'notStarted';
  }
}
