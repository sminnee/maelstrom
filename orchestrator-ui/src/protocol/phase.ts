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
 * The phase a task's `command` puts it in. `phaseForTask` is the full reading;
 * the phase is never sent on the wire.
 */
export function phaseForCommand(command: string): Phase | null {
  // An execute task runs no skill, so no command is the ordinary build case.
  if (command === '') return 'build';
  return (PHASES as Record<string, Phase>)[command] ?? null;
}

/**
 * The phase a task is in now. A plan-mode build task plans until its agent
 * leaves plan mode — which plan approval does — and then builds. Any other
 * phase, or none, is the command's alone: the mode does not change that work.
 *
 * With no agent, only a task not yet started reads as planning. A finished
 * task's agent can leave the world, and the task did not go back to planning.
 */
export function phaseForTask(
  task: Pick<Task, 'command' | 'mode' | 'status'>,
  agent?: Pick<Agent, 'permissionMode'>,
): Phase | null {
  const phase = phaseForCommand(task.command);
  if (task.mode !== 'plan' || phase !== 'build') return phase;
  if (!agent) return task.status === 'todo' ? 'plan' : phase;
  return agent.permissionMode === '' || agent.permissionMode === 'plan' ? 'plan' : phase;
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
