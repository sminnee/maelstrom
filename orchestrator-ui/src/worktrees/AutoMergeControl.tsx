import { useSetAutoMerge } from '../api/worktrees';
import type { PrState, Worktree } from '../protocol/entities';
import { AppButton } from '../ui/AppButton';
import styles from './AutoMergeControl.module.css';

/** The **PR states** in which GitHub has not yet said ready, nor said it will not be. */
const WAITING: readonly (PrState | '')[] = ['ci-running', 'unknown', 'checks-unreadable'];

/**
 * Turns **Auto-merge** on or off for a worktree's pull request. When it draws:
 * `docs/dev/orchestrator-ui.md`, "The worktree area".
 */
export function AutoMergeControl({ worktree }: { worktree: Worktree }) {
  const setAutoMerge = useSetAutoMerge();
  const on = worktree.prAutoMerge;
  const open = worktree.prState !== 'merged';
  if (!worktree.prNumber || worktree.prDraft || !worktree.prAutoMergeAllowed) return null;
  if (!(open && on) && !WAITING.includes(worktree.prState)) return null;
  const differs = !on && worktree.prMatch === 'differ';

  return (
    <AppButton
      variant="quiet"
      role="switch"
      aria-checked={on}
      className={styles.switch}
      disabled={differs}
      title={
        differs
          ? 'The local branch differs from the PR. Sync it first.'
          : 'GitHub merges the PR when its checks pass.'
      }
      onClick={() => setAutoMerge.mutateAsync({ worktreeId: worktree.id, on: !on })}
    >
      <input type="checkbox" checked={on} readOnly aria-hidden tabIndex={-1} />
      Auto-merge
    </AppButton>
  );
}
