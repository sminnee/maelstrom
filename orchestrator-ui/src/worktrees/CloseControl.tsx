import { useCloseWorktree, useForceCloseWorktree, useTrashWorktree } from '../api/worktrees';
import { useWorld } from '../api/useWorld';
import type { Worktree } from '../protocol/entities';
import { canClose, trackedAgents } from '../selectors/worktrees';
import { SplitButton, type SplitOption } from '../ui/SplitButton';
import { trashConfirm } from './trashConfirm';

/**
 * The worktree's close: Close, **Shelve** and **Trash**. Held while an agent
 * runs in the worktree. A card does not draw it then at all — see
 * `docs/dev/orchestrator-ui.md`, "The worktree area".
 */
export function CloseControl({ worktree }: { worktree: Worktree }) {
  const { world } = useWorld();
  const close = useCloseWorktree();
  const shelve = useForceCloseWorktree();
  const trash = useTrashWorktree();
  if (!canClose(worktree)) return null;

  const running = trackedAgents(world, worktree.id).length;
  const held = {
    disabled: running > 0,
    detail:
      running > 0
        ? `${running} ${running === 1 ? 'agent' : 'agents'} still running in ${worktree.nato}`
        : undefined,
  };
  const target = { worktreeId: worktree.id };
  const options: SplitOption[] = [
    { label: 'Close', processing: 'Closing…', ...held, run: () => close.mutateAsync(target) },
    {
      label: 'Shelve',
      processing: 'Shelving…',
      ...held,
      confirm: {
        question: `Shelve ${worktree.nato}? Work in progress is committed. Unmerged work gets a task to reopen it.`,
        confirm: 'Shelve it',
      },
      run: () => shelve.mutateAsync(target),
    },
    {
      label: 'Trash',
      processing: 'Trashing…',
      ...held,
      confirm: trashConfirm(worktree),
      run: () => trash.mutateAsync(target),
    },
  ];
  return <SplitButton variant="quiet" menuLabel="More close actions" options={options} />;
}
