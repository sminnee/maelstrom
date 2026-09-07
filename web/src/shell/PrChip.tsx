import type { Worktree } from '../protocol/entities';
import { describePrState, prStateKey } from '../selectors/status';
import styles from './PrChip.module.css';

/**
 * `#278` with a state dot, in a collapsed node's or a deck row's meta line.
 *
 * A reading, not a link: the whole node is already a click target, and the
 * expanded card carries the link that opens the PR.
 */
export function PrChip({ worktree, className }: { worktree?: Worktree; className?: string }) {
  if (!worktree?.prNumber) return null;
  const state = describePrState(worktree.prState, worktree.prDraft);
  return (
    <span
      className={`${styles.pr}${className ? ` ${className}` : ''}`}
      data-pr-state={prStateKey(worktree.prState, worktree.prDraft)}
      title={state}
    >
      <span className={styles.dot} aria-hidden="true" />#{worktree.prNumber}
    </span>
  );
}
