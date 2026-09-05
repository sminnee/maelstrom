import type { Attention } from './attention';
import { isOpen } from './attention';
import type { Agent, Task, TaskStatus } from './entities';

/** Where a node sits in the left-to-right progression of work. */
export type Zone = 'done' | 'running' | 'notStarted';

/** The zones in board order, leftmost first. The index is the progress rank. */
export const ZONES = ['done', 'running', 'notStarted'] as const;

/** How a node draws: the state axis, orthogonal to phase. */
export type NodeState =
  | 'queued'
  | 'ready'
  | 'working'
  | 'needs-attention'
  | 'idle'
  | 'finalising'
  | 'done'
  | 'cancelled'
  | 'exited';

/** Which way the task file and the agent record disagree. Mirrors `RECONCILE_*`. */
export type DriftKind = 'finished' | 'never-ran' | 'orphan-session';

/** One reading of a node's progress: how it draws, what it says, what disagrees. */
export interface Progress {
  state: NodeState;
  /** The state in words. Raw agent states such as `awaiting-question` never reach the screen. */
  words: string;
  /** Null when the task file and the agent agree, or there is no task. */
  drift: DriftKind | null;
  /** The status the task should move to, or null when there is nothing to correct. */
  fixStatus: TaskStatus | null;
  /** Whether `words` only restate `task.status`, so a view with a status control can drop them. */
  echoesStatus: boolean;
}

/** The task fields a reading needs. A free agent passes `undefined`. */
type TaskFacts = Pick<Task, 'id' | 'status' | 'actionable'>;

/**
 * The one reading of a node's state, its words, and the disagreement between
 * the task file and the agent observed on it.
 *
 * The agent wins when the two disagree: an agent state is observed from
 * events, a task status is a file that goes out of date. The exception is a
 * terminal task, where the session is finalising the work, not disagreeing.
 *
 * `attention` is required, not defaulted: a caller that passes none reads a
 * blocked agent as idle, and every view that draws a node holds the table.
 */
export function progressOf(
  task: TaskFacts | undefined,
  agent: Agent | undefined,
  attention: readonly Attention[],
): Progress {
  const state = nodeState(task, agent, attention);
  const drift = driftOf(task, agent);
  return {
    state,
    words: describeState(state, task, agent),
    drift,
    fixStatus: drift ? fixStatusFor(drift, task) : null,
    echoesStatus: !drift && restatesStatus(task, state),
  };
}

function nodeState(
  task: TaskFacts | undefined,
  agent: Agent | undefined,
  attention: readonly Attention[],
): NodeState {
  // The task closes when the PR is pushed, and watch-pr keeps running to take
  // CI green. That tail is the ordinary end of a task, so it has its own
  // state rather than reading as a task status fighting its agent.
  // A dead agent is the stronger signal: its attention item still counts in
  // the chip, but the node draws red rather than orange. An unknown exit code
  // counts as abnormal: only an observed 0 is a clean exit.
  // The task closes when the PR is pushed, and watch-pr keeps running to take
  // CI green. That tail is the ordinary end of a task, so it draws as its own
  // state. Once nothing is running the task is history, and an item still open
  // against it is stale bookkeeping rather than the user's turn.
  const live = agent !== undefined && isTurning(agent);
  const terminal = task?.status === 'done' || task?.status === 'cancelled';
  if (terminal && !live) return task?.status === 'done' ? 'done' : 'cancelled';
  if (agent?.state === 'exited' && agent.exitCode !== 0) return 'exited';
  const open = attention.some(
    (item) =>
      isOpen(item) && ((task && item.taskId === task.id) || (agent && item.agentId === agent.id)),
  );
  // A running agent asking for the user reaches them however the task is filed.
  if (open) return 'needs-attention';
  if (terminal) return 'finalising';
  // Ready is the one waiting state the operator can act on, so it is the one
  // that gets a hue: queued is waiting on other work, ready is waiting on them.
  if (!agent) return task?.actionable ? 'ready' : 'queued';
  if (agent.state === 'exited') return 'idle';
  if (agent.state === 'processing') return 'working';
  return 'idle';
}

/**
 * The state in words. It switches on the state, not on a second walk of the
 * task and the agent, so the words and the way the node draws cannot disagree.
 * The task only ever supplies the noun for a state that does not name itself:
 * `queued` covers waiting, blocked and template alike.
 */
function describeState(
  state: NodeState,
  task: TaskFacts | undefined,
  agent: Agent | undefined,
): string {
  switch (state) {
    case 'done':
      return 'Done';
    case 'cancelled':
      return 'Cancelled';
    case 'exited':
      return agent?.exitCode === null || agent?.exitCode === undefined
        ? 'Exited (unknown code)'
        : `Exited (code ${agent.exitCode})`;
    case 'working':
      return 'Working';
    case 'finalising':
      return 'Finalising';
    case 'ready':
      return 'Ready to launch';
    case 'queued':
      return queuedWords(task);
    case 'needs-attention':
      return needsYouWords(agent);
    case 'idle':
      // A clean exit is the work finishing, not a fault, so it says so.
      return agent?.state === 'exited' ? 'Finished' : 'Idle';
  }
}

/** The words a waiting node uses. Blocked and template say what they are. */
function queuedWords(task: TaskFacts | undefined): string {
  if (task?.status === 'blocked') return 'Blocked';
  if (task?.status === 'template') return 'Template';
  return 'Queued';
}

function needsYouWords(agent: Agent | undefined): string {
  switch (agent?.state) {
    case 'awaiting-question':
      return 'Needs you · question';
    case 'awaiting-permission':
      return 'Needs you · permission';
    case 'awaiting-plan-review':
      return 'Needs you · plan review';
    default:
      return 'Needs you';
  }
}

/**
 * Whether the agent is turning, or waiting on the operator. Narrower than
 * `isLive` in `selectors/graph.ts`, which asks only whether the process is up
 * and so counts an idle agent.
 */
function isTurning(agent: Agent): boolean {
  return agent.state === 'processing' || agent.state.startsWith('awaiting-');
}

/**
 * Which way the two fields disagree, mirroring `reconcile()` in `task.py`.
 * An agent record means the task ran and stopped; no record means it never
 * launched. A free agent has no task to disagree with.
 */
function driftOf(task: TaskFacts | undefined, agent: Agent | undefined): DriftKind | null {
  if (!task) return null;
  if (task.status === 'in-progress') {
    if (!agent) return 'never-ran';
    // A stopped session just means the work ended: there is no
    // completed-versus-aborted signal on disk.
    return agent.state === 'exited' ? 'finished' : null;
  }
  if (!agent) return null;
  // Divergence from `reconcile()`: an agent on a terminal task is the happy
  // path, not an orphan row. A live one is finalising the work; an idle one
  // is the session outliving the PR push.
  if (task.status === 'done' || task.status === 'cancelled') return null;
  return isTurning(agent) || agent.state === 'idle' ? 'orphan-session' : null;
}

/**
 * The status the task should move to. Mirrors `ReconcileRow.fix_status`, with
 * one refusal: `never-ran` offers none.
 *
 * Python splits `never-ran` from `finished` on a transcript on disk. The
 * client splits them on whether an agent record exists, and a server that has
 * just restarted has no record of an agent that ran before it. So finished
 * work can present as `never-ran`, and `todo` would send it back to the queue.
 * The card still names what the client saw; the operator moves the task.
 */
function fixStatusFor(drift: DriftKind, task: TaskFacts | undefined): TaskStatus | null {
  switch (drift) {
    case 'finished':
      return 'done';
    case 'never-ran':
      return null;
    case 'orphan-session':
      // A terminal task with a lingering session is listed, not auto-corrected.
      return task?.status === 'done' || task?.status === 'cancelled' ? null : 'in-progress';
  }
}

/**
 * Whether the words only restate the task's status, so a view that already
 * shows the status can drop them rather than say the same thing twice.
 */
function restatesStatus(task: TaskFacts | undefined, state: NodeState): boolean {
  if (!task) return false;
  // The words say what the status cannot while an agent still carries the PR.
  if (state === 'finalising') return false;
  if (task.status === 'done' || task.status === 'cancelled') return true;
  return task.status === 'blocked' && state === 'queued';
}

/**
 * The disagreement in one sentence, naming both values. A view that shows the
 * task status beside it still gets both, because the sentence is what says
 * which way the two disagree.
 */
export function driftSentence(progress: Progress, status: TaskStatus): string {
  switch (progress.drift) {
    case 'finished':
      return 'The agent has stopped, but the task is still in-progress.';
    case 'never-ran':
      return 'The task is in-progress, but this session has seen no agent run it.';
    case 'orphan-session':
      return progress.fixStatus
        ? `An agent is running, but the task is ${status}.`
        : `An agent is still running on a ${status} task.`;
    case null:
      return '';
  }
}

/** The caret's label. It has no adjacent text repeating what it says. */
export function driftLabel(progress: Progress): string {
  switch (progress.drift) {
    case 'finished':
      return 'the agent has stopped, but the task is still in-progress';
    case 'never-ran':
      return 'the task is in-progress, but no agent ever ran it';
    case 'orphan-session':
      return 'an agent is running, but the task says otherwise';
    case null:
      return '';
  }
}

/** What the fix button offers, for each status a fix moves a task to. */
export function driftFixLabel(status: TaskStatus): string {
  switch (status) {
    case 'done':
      return 'Mark done';
    case 'todo':
      return 'Send back to todo';
    case 'in-progress':
      return 'Mark in-progress';
    default:
      return `Set ${status}`;
  }
}

/** Which of the three progress zones a state falls in. See `Zone` in `CONTEXT.md`. */
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
    case 'finalising':
    case 'exited':
      return 'running';
    case 'ready':
    case 'queued':
      return 'notStarted';
  }
}
