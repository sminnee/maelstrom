import type { TabGroup } from '../selectors/tabs';
import { WorktreeCommands, WorktreeLinks } from '../worktrees/WorktreeControls';
import styles from './WorktreeBar.module.css';

/** The worktree in view's controls, above its tabs. A group of no worktree has none. */
export function WorktreeBar({ group }: { group: TabGroup | null }) {
  const worktree = group?.worktree;
  if (!worktree) return null;
  return (
    <div className={styles.bar} data-testid="worktree-bar">
      <span className={`${styles.branch} truncate`} title={worktree.branch}>
        {worktree.branch}
      </span>
      <span className={styles.controls}>
        <WorktreeLinks worktree={worktree} />
        <WorktreeCommands worktree={worktree} />
      </span>
    </div>
  );
}
