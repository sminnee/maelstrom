import type { NodeProps, Node } from '@xyflow/react';
import type { GraphGroup } from '../selectors/graph';
import styles from './GroupNode.module.css';

export type GroupFlowNode = Node<{ group: GraphGroup }, 'group'>;

export function GroupNode({ data }: NodeProps<GroupFlowNode>) {
  const { group } = data;
  return (
    <div className={styles.group} data-testid="group-node" data-group-id={group.id}>
      <div className={styles.label}>
        <span>{group.label}</span>
        {group.sublabel && <span className={styles.sublabel}>{group.sublabel}</span>}
      </div>
    </div>
  );
}
