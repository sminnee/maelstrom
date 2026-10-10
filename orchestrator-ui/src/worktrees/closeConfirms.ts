import type { Worktree } from '../protocol/entities';
import type { SplitOption } from '../ui/SplitButton';

type Confirm = NonNullable<SplitOption['confirm']>;

/*
 * What the worktree endings ask first. The close control and the node card's
 * Dismiss menu both ask these, so the two cannot drift.
 */

export function shelveConfirm(worktree: Worktree): Confirm {
  return {
    question: `Shelve ${worktree.nato}? Work in progress is committed. Unmerged work gets a task to reopen it.`,
    confirm: 'Shelve it',
  };
}

export function trashConfirm(worktree: Worktree): Confirm {
  return {
    // A detached worktree has no branch to name.
    question: `Trash ${worktree.branch || worktree.nato}? Its PR closes and the branch moves to trash/.`,
    confirm: 'Trash it',
  };
}

export function removeConfirm(worktree: Worktree): Confirm {
  return {
    question: `Delete ${worktree.nato}? The checkout goes; the branch stays.`,
    confirm: 'Delete it',
  };
}
