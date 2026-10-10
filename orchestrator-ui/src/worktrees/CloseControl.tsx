import {
  useCloseWorktree,
  useForceCloseWorktree,
  useRemoveWorktree,
  useTrashWorktree,
} from '../api/worktrees';
import { useWorld } from '../api/useWorld';
import type { Worktree } from '../protocol/entities';
import { isMain, trackedAgents } from '../selectors/worktrees';
import { actionIcon } from '../ui/actionIcons';
import { SplitButton, type SplitOption } from '../ui/SplitButton';
import { removeConfirm, shelveConfirm, trashConfirm } from './closeConfirms';

/**
 * The worktree's close: Close, **Shelve**, **Trash** and Delete (**Remove**).
 * Held while an agent runs in the worktree. See `docs/dev/orchestrator-ui.md`,
 * "The worktree area".
 */
export function CloseControl({ worktree }: { worktree: Worktree }) {
  const { world } = useWorld();
  const close = useCloseWorktree();
  const shelve = useForceCloseWorktree();
  const trash = useTrashWorktree();
  const remove = useRemoveWorktree();
  if (isMain(worktree)) return null;

  const running = trackedAgents(world, worktree.id).length;
  const held = {
    disabled: running > 0,
    detail:
      running > 0
        ? `${running} ${running === 1 ? 'agent' : 'agents'} still running in ${worktree.nato}`
        : undefined,
  };
  const target = { worktreeId: worktree.id };
  const deleteOption: SplitOption = {
    label: 'Delete',
    icon: actionIcon('removeWorktree'),
    processing: 'Deleting…',
    // A plain button has no menu item to say why it is held, and the remove
    // stops any agent itself.
    ...(worktree.isClosed ? {} : held),
    confirm: removeConfirm(worktree),
    run: () => remove.mutateAsync(target),
  };
  const options: SplitOption[] = worktree.isClosed
    ? [deleteOption]
    : [
        {
          label: 'Close',
          icon: actionIcon('closeWorktree'),
          processing: 'Closing…',
          ...held,
          run: () => close.mutateAsync(target),
        },
        {
          label: 'Shelve',
          icon: actionIcon('archive'),
          processing: 'Shelving…',
          ...held,
          confirm: shelveConfirm(worktree),
          run: () => shelve.mutateAsync(target),
        },
        {
          label: 'Trash',
          icon: actionIcon('delete'),
          processing: 'Trashing…',
          ...held,
          confirm: trashConfirm(worktree),
          run: () => trash.mutateAsync(target),
        },
        deleteOption,
      ];
  return <SplitButton variant="quiet" menuLabel="More close actions" options={options} />;
}
