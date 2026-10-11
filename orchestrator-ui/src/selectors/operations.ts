import type { Operation, OperationKind, OperationStep } from '../protocol/entities';

/** What each kind of **Operation** is called, as the head of a line about it. */
export const KIND_NAMES: Record<OperationKind, string> = {
  close: 'Close',
  shelve: 'Shelve',
  trash: 'Trash',
  delete: 'Delete',
  sync: 'Sync',
  merge: 'Merge',
  env: 'Environment',
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
        [operationLine(op), op.worktreeId, KIND_NAMES[op.kind], op.taskId ?? '', op.agentId ?? '']
          .join(' ')
          .toLowerCase()
          .includes(needle)),
  );
}
