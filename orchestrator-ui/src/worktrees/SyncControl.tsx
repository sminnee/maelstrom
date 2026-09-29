import { useSyncWorktree, type SyncMode } from '../api/worktrees';
import type { Worktree } from '../protocol/entities';
import { SplitButton, type SplitOption } from '../ui/SplitButton';

/**
 * One control for `mael sync` on a worktree. A plain sync aborts on a
 * conflict; autorepair starts a repair session. `_main` gets the plain sync
 * only, since a squash of the main checkout makes no sense.
 */
export function SyncControl({ worktree }: { worktree: Worktree }) {
  const sync = useSyncWorktree();
  const option = (label: string, processing: string, mode: SyncMode): SplitOption => ({
    label,
    processing,
    run: () => sync.mutateAsync({ worktreeId: worktree.id, mode }),
  });

  const plain = option('Sync branch', 'Syncing…', 'plain');
  const options =
    worktree.nato === '_main'
      ? [plain]
      : [
          plain,
          option('Sync & squash', 'Squashing…', 'squash'),
          option('Sync & autorepair', 'Syncing…', 'autorepair'),
        ];

  return <SplitButton variant="quiet" menuLabel="More sync actions" options={options} />;
}
