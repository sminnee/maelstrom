import type { Worktree } from '../protocol/entities';
import { useNewWork } from '../nav/useOverlays';
import { useCard } from '../nav/useCard';
import { AppButton } from '../ui/AppButton';
import { WorktreeSection } from '../worktrees/WorktreeSection';
import { CanvasCard } from './CanvasCard';
import { EMPTY_BOX } from './layout';
import { actionIcon } from '../ui/actionIcons';
import styles from './WorktreeCard.module.css';

/** The card's width in flow units. Its height comes from its content. */
export const WORKTREE_CARD_WIDTH = 360;

/**
 * The **Worktree card**: a worktree's controls on the canvas, for a worktree
 * with a node and for one with none. It is the node card's worktree area
 * under a header, plus the one thing a node card has no need of: starting a
 * free agent here.
 */
export function WorktreeCard({
  worktree,
  position,
  open,
  onClosed,
}: {
  worktree: Worktree;
  position: { x: number; y: number };
  open: boolean;
  onClosed: () => void;
}) {
  const { collapse } = useCard();
  const newWork = useNewWork();
  return (
    <CanvasCard
      label={`Worktree ${worktree.project} ${worktree.nato}`}
      className={styles.card}
      position={position}
      from={EMPTY_BOX}
      open={open}
      onClosed={onClosed}
    >
      <header className={styles.header}>
        <div className={styles.titleBlock}>
          <h2 className={styles.title}>{worktree.nato}</h2>
          <span className={styles.project}>{worktree.project}</span>
        </div>
        <button
          type="button"
          className={styles.close}
          aria-label="Collapse"
          onClick={() => collapse()}
        >
          {actionIcon('close')}
        </button>
      </header>
      <WorktreeSection worktree={worktree} />
      {!worktree.isClosed && (
        <div className={styles.commands}>
          <AppButton
            variant="primary"
            icon={actionIcon('freeAgent')}
            disabled={!worktree.branch}
            title={worktree.branch ? undefined : 'A detached worktree has no branch to start on'}
            onClick={() => {
              // The form is the one thing open: the card would sit behind it, so
              // opening on a seed closes it.
              newWork.open({
                kind: 'agent',
                project: worktree.project,
                branch: worktree.branch,
              });
            }}
          >
            Start free agent
          </AppButton>
        </div>
      )}
    </CanvasCard>
  );
}
