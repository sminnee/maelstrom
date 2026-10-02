import type { Worktree } from '../protocol/entities';
import type { SplitOption } from '../ui/SplitButton';

/** What a trash asks first. The node card's Terminate chain asks the same. */
export function trashConfirm(worktree: Worktree): NonNullable<SplitOption['confirm']> {
  return {
    // A detached worktree has no branch to name.
    question: `Trash ${worktree.branch || worktree.nato}? Its PR closes and the branch moves to trash/.`,
    confirm: 'Trash it',
  };
}
