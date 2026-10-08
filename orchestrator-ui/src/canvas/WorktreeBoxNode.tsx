import type { NodeProps, Node } from '@xyflow/react';
import { branchLabel } from '../selectors/worktrees';
import { useCard } from '../nav/useCard';
import type { WorktreeBox } from './layout';
import styles from './WorktreeBoxNode.module.css';

export type WorktreeBoxFlowNode = Node<{ box: WorktreeBox }, 'worktreeBox'>;

/**
 * One **Worktree box**: a dashed outline, and a label that names the worktree
 * and its branch. The label is the box's one control: it opens the
 * **Worktree card**.
 */
export function WorktreeBoxNode({ data }: NodeProps<WorktreeBoxFlowNode>) {
  const { worktree, empty } = data.box;
  const { expandedWorktreeId, open } = useCard();
  const expanded = expandedWorktreeId === worktree.id;
  return (
    <div
      className={styles.box}
      data-testid="worktree-box"
      data-worktree-id={worktree.id}
      data-empty={empty}
    >
      <button
        type="button"
        className={`${styles.label} nodrag nopan`}
        title={worktree.branch || undefined}
        aria-expanded={expanded}
        onClick={() => open({ kind: 'worktree', id: worktree.id })}
      >
        <span className={styles.name}>{worktree.nato}</span>{' '}
        <span className={styles.branch}>{branchLabel(worktree)}</span>
      </button>
    </div>
  );
}
