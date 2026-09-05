import type { NodeProps, Node } from '@xyflow/react';
import { useCloseWorktree } from '../api/worktrees';
import type { Worktree } from '../protocol/entities';
import type { GraphGroup } from '../selectors/graph';
import { AppButton } from '../ui/AppButton';
import styles from './GroupNode.module.css';

export type GroupFlowNode = Node<{ group: GraphGroup }, 'group'>;

export function GroupNode({ data }: NodeProps<GroupFlowNode>) {
  const { group } = data;
  return (
    <div className={styles.group} data-testid="group-node" data-group-id={group.id}>
      <div className={styles.label}>
        <span>{group.label}</span>
        {group.sublabel && <span className={styles.sublabel}>{group.sublabel}</span>}
        {group.worktree && <CloseWorktree worktree={group.worktree} />}
      </div>
    </div>
  );
}

/**
 * The lane's close, on the worktree the lane stands for. `_main` never closes,
 * so it is offered no button.
 */
function CloseWorktree({ worktree }: { worktree: Worktree }) {
  const close = useCloseWorktree();
  if (worktree.nato === '_main') return null;
  return (
    <AppButton
      variant="quiet"
      className={styles.close}
      title={`Close ${worktree.nato}`}
      processingChildren="Closing…"
      onClick={() => close.mutateAsync({ worktreeId: worktree.id })}
    >
      Close
    </AppButton>
  );
}
