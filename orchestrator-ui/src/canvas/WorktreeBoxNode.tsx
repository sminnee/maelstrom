import type { NodeProps, Node } from '@xyflow/react';
import type { WorktreeBox } from './layout';
import styles from './WorktreeBoxNode.module.css';

export type WorktreeBoxFlowNode = Node<{ box: WorktreeBox }, 'worktreeBox'>;

/** One **Worktree box**: a dotted outline and a name. It is not a control. */
export function WorktreeBoxNode({ data }: NodeProps<WorktreeBoxFlowNode>) {
  const { worktree, empty } = data.box;
  return (
    <div
      className={styles.box}
      data-testid="worktree-box"
      data-worktree-id={worktree.id}
      data-empty={empty}
    >
      <span className={styles.label}>{worktree.nato}</span>
    </div>
  );
}
