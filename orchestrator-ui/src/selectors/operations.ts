import type { Operation, OperationKind, OperationStep } from '../protocol/entities';
import type { AgentId, OperationId, TaskId, WorktreeId } from '../protocol/ids';
import type { GraphNode } from './graph';

/** What each kind of **Operation** is called, as the head of a line about it. */
export const KIND_NAMES: Record<OperationKind, string> = {
  close: 'Close',
  shelve: 'Shelve',
  trash: 'Trash',
  delete: 'Delete',
  sync: 'Sync',
  merge: 'Merge',
  env: 'Environment',
  dismiss: 'Dismiss',
  terminate: 'Terminate',
  resume: 'Resume',
  launch: 'Launch',
  start: 'Start',
  create: 'Create',
  plan: 'Plan',
};

/** An operation that ended **Refused** or failed. */
export const endedBadly = (op: Operation) => op.state === 'refused' || op.state === 'failed';

/** An operation that ended **Refused** or failed, and the user has not looked at: **Unseen**. */
export const isUnseen = (op: Operation) => endedBadly(op) && !op.seen;

/** What the console pins on top: what runs, and what is **Unseen**. */
export const isPinned = (op: Operation) => op.state === 'running' || isUnseen(op);

/** The step that runs now, or the one that refused or failed. */
export function currentStep(op: Operation): OperationStep | undefined {
  return op.steps.find(
    (s) => s.state === 'running' || s.state === 'refused' || s.state === 'failed',
  );
}

/** How many of the operation's steps are done. */
export const stepsDone = (op: Operation) => op.steps.filter((s) => s.state === 'done').length;

/**
 * One line about an operation. While it runs, and once done, its own words:
 * `Closing alpha`. Once ended badly, the kind and how it ended, then why:
 * `Close refused · Worktree has 2 commit(s) not merged to origin/main`. The
 * step's words are the bare reason, where the operation's may restate the kind.
 */
export function operationLine(op: Operation): string {
  if (!endedBadly(op)) return op.words;
  const reason = currentStep(op)?.words || op.words;
  return `${KIND_NAMES[op.kind]} ${op.state} · ${reason}`;
}

/**
 * What an operation acts on: its worktree, else its task, else its agent. A
 * launch knows none of them at first, so it names its project and links nowhere.
 */
export function operationTarget(op: Operation): {
  label: string;
  card: { kind: 'worktree' | 'task' | 'agent'; id: string } | null;
} {
  if (op.worktreeId !== null) {
    return { label: op.worktreeId, card: { kind: 'worktree', id: op.worktreeId } };
  }
  if (op.taskId !== null) return { label: op.taskId, card: { kind: 'task', id: op.taskId } };
  if (op.agentId !== null) return { label: op.agentId, card: { kind: 'agent', id: op.agentId } };
  return { label: op.project, card: null };
}

const newestFirst = (a: Operation, b: Operation) => b.startedAt.localeCompare(a.startedAt);

/**
 * The console's order: running and **Unseen** on top, then the rest, each
 * newest first. `pinned` says what goes on top, for a list that holds an
 * operation there after it is seen.
 */
export function consoleOrder(
  ops: readonly Operation[],
  pinned: (op: Operation) => boolean = isPinned,
): Operation[] {
  return [
    ...ops.filter(pinned).sort(newestFirst),
    ...ops.filter((op) => !pinned(op)).sort(newestFirst),
  ];
}

/** The console's filter row: what runs, what ended badly, or everything. */
export type ConsoleFilter = 'running' | 'problems' | 'all';

/** The operations `filter` and `text` keep, in the order given. */
export function filterOperations(
  ops: readonly Operation[],
  filter: ConsoleFilter,
  text: string,
): Operation[] {
  const needle = text.trim().toLowerCase();
  return ops.filter(
    (op) =>
      (filter === 'all' || (filter === 'running' ? op.state === 'running' : endedBadly(op))) &&
      (!needle ||
        [
          operationLine(op),
          operationTarget(op).label,
          KIND_NAMES[op.kind],
          op.taskId ?? '',
          op.agentId ?? '',
        ]
          .join(' ')
          .toLowerCase()
          .includes(needle)),
  );
}

/** The kinds that end a unit's time on the board. Only these mark a node through its worktree. */
export const ENDING_KINDS: ReadonlySet<OperationKind> = new Set([
  'close',
  'shelve',
  'trash',
  'delete',
  'dismiss',
]);

/** What each kind of operation does, as a worktree box says it while it runs. */
export const KIND_DOING: Record<OperationKind, string> = {
  close: 'closing',
  shelve: 'shelving',
  trash: 'trashing',
  delete: 'deleting',
  sync: 'syncing',
  merge: 'merging',
  env: 'env',
  dismiss: 'dismissing',
  terminate: 'terminating',
  resume: 'resuming',
  launch: 'launching',
  start: 'starting',
  create: 'creating',
  plan: 'planning',
};

/**
 * What an operation can mark on the board: a worktree (its box and its table
 * row), or a node, which names its worktree, task and agent where it has them.
 */
export type Unit =
  | { worktreeId: WorktreeId }
  | { node: { worktreeId: WorktreeId | null; taskId: TaskId | null; agentId: AgentId | null } }
  | { operationId: OperationId };

/** Whether `op` acts on `unit`, whatever its state. */
export function concerns(op: Operation, unit: Unit): boolean {
  if ('operationId' in unit) return op.id === unit.operationId;
  if ('worktreeId' in unit) return op.worktreeId === unit.worktreeId;
  const { worktreeId, taskId, agentId } = unit.node;
  return (
    (taskId !== null && op.taskId === taskId) ||
    (agentId !== null && op.agentId === agentId) ||
    (worktreeId !== null && op.worktreeId === worktreeId && ENDING_KINDS.has(op.kind))
  );
}

/**
 * The operation that marks a unit: the running one, else the newest **Unseen**
 * one. A seen or done operation leaves the unit clean.
 */
export function unitOperation(ops: readonly Operation[], unit: Unit): Operation | undefined {
  const mine = ops.filter((op) => concerns(op, unit)).sort(newestFirst);
  return mine.find((op) => op.state === 'running') ?? mine.find(isUnseen);
}

/**
 * A running operation's step count: `3/5` while the third of five runs. It
 * holds at `5/5` when the last step is done before the operation ends.
 */
const stepCount = (op: Operation) =>
  `${Math.min(stepsDone(op) + 1, op.steps.length)}/${op.steps.length}`;

/** What a node, a deck row and a table row say about the operation that marks them. */
export function unitWords(op: Operation): string {
  return op.state === 'running' ? `${op.words} · ${stepCount(op)}` : operationLine(op);
}

/** What a worktree box adds to its label: `closing 2/4`, or `close refused`. */
export function boxWords(op: Operation): string {
  return op.state === 'running'
    ? `${KIND_DOING[op.kind]} ${stepCount(op)}`
    : `${KIND_NAMES[op.kind].toLowerCase()} ${op.state}`;
}

/** The unit a board node stands for. The canvas node and the deck row both build it here. */
export function nodeUnit(node: GraphNode): Unit {
  // An opening node is its operation: it has no task or agent to be found by.
  if (node.operation) return { operationId: node.operation.id };
  return {
    node: {
      worktreeId: node.worktree?.id ?? node.agent?.worktreeId ?? null,
      taskId: node.task?.id ?? null,
      agentId: node.agent?.id ?? null,
    },
  };
}
