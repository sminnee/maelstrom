import type { Worktree } from '../protocol/entities';
import { worktreePr, type PrReading } from '../selectors/cardPr';
import { branchLabel } from '../selectors/worktrees';
import { WorktreeCommands, WorktreeLinks } from './WorktreeControls';
import styles from './WorktreeSection.module.css';

/**
 * The worktree area: what belongs to a worktree and not to an agent. Its name
 * and branch, the links out of it, then the commands on it.
 *
 * The node card and the **Worktree card** both draw this, so the two cannot
 * drift. `pr` is the PR to show, as `WorktreeLinks` reads it.
 *
 * The close shows only while no agent runs in the worktree — see
 * `docs/dev/orchestrator-ui.md`, "The worktree area".
 */
export function WorktreeSection({
  worktree,
  pr = worktreePr(worktree),
}: {
  worktree: Worktree;
  pr?: PrReading | null;
}) {
  return (
    <section className={styles.section} aria-label="Worktree" data-testid="worktree-section">
      <span className={styles.head}>Worktree</span>
      <div className={`${styles.name} wrap`} data-testid="worktree-name" title={worktree.branch}>
        <span className={styles.nato}>{worktree.nato}</span>
        {' · '}
        <span>{branchLabel(worktree)}</span>
      </div>
      <div className={styles.links}>
        <WorktreeLinks worktree={worktree} pr={pr} />
      </div>
      <div className={styles.commands}>
        <WorktreeCommands worktree={worktree} busyClose="hide" />
      </div>
    </section>
  );
}
