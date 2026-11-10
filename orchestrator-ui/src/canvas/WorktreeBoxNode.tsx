import type { NodeProps, Node } from '@xyflow/react';
import { branchLabel } from '../selectors/worktrees';
import { useCard } from '../nav/useCard';
import { useUnitOperation } from '../console/useConsole';
import { boxWords, endedBadly } from '../selectors/operations';
import type { WorktreeBox } from './layout';
import styles from './WorktreeBoxNode.module.css';

/**
 * `name` is the worktree's `worktreeName`, and `usedBy` its `usedByParent`.
 * The canvas reads them from the world, so a box does not subscribe to it.
 */
export type WorktreeBoxFlowNode = Node<
  { box: WorktreeBox; name: string; usedBy: string },
  'worktreeBox'
>;

/**
 * One **Worktree box**: a dashed outline, and a label that names the worktree,
 * its branch, any operation that marks it, and the **Parent server** whose
 * **Delegated agent** works in it. The label is the box's one
 * control: it opens the **Worktree card**.
 */
export function WorktreeBoxNode({ data }: NodeProps<WorktreeBoxFlowNode>) {
  const { worktree, empty } = data.box;
  const { name, usedBy } = data;
  const { expandedWorktreeId, open } = useCard();
  const expanded = expandedWorktreeId === worktree.id;
  const op = useUnitOperation({ worktreeId: worktree.id });
  return (
    <div
      className={styles.box}
      data-testid="worktree-box"
      data-worktree-id={worktree.id}
      data-empty={empty}
      data-operation={op?.state}
    >
      <button
        type="button"
        className={`${styles.label} nowrap nodrag nopan`}
        title={worktree.branch || undefined}
        aria-expanded={expanded}
        onClick={(e) => {
          // A click on the box collapses the card; this one opens a card.
          e.stopPropagation();
          open({ kind: 'worktree', id: worktree.id });
        }}
      >
        <span className={styles.name}>{name}</span>{' '}
        <span className={`${styles.branch} truncate`}>{branchLabel(worktree)}</span>
        {op && (
          <span className={styles.operation} data-ended={endedBadly(op) || undefined}>
            {' · '}
            {boxWords(op)}
          </span>
        )}
        {usedBy && <span className={styles.usedBy}>{` · in use by ${usedBy}`}</span>}
      </button>
    </div>
  );
}
