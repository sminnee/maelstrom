import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import type { GraphNode } from '../selectors/graph';
import styles from './TaskNode.module.css';

export type TaskFlowNode = Node<{ node: GraphNode; focused: boolean }, 'task'>;

export function TaskNode({ data }: NodeProps<TaskFlowNode>) {
  const { node, focused } = data;
  return (
    <div
      className={styles.node}
      data-testid="task-node"
      data-task-id={node.id}
      data-phase={node.phase}
      data-state={node.state}
      data-focused={focused || undefined}
    >
      <Handle type="target" position={Position.Left} className={styles.handle} />
      <div className={styles.head}>
        <span className={styles.id}>{node.id}</span>
        <span className={styles.phase}>{node.phase}</span>
        {node.state === 'needs-attention' && (
          <span className={styles.badge} aria-label="needs attention">
            !
          </span>
        )}
        {node.state === 'done' && <span className={styles.tick}>✓</span>}
      </div>
      <div className={styles.title}>{node.task.title}</div>
      <div className={styles.foot}>
        {node.reason ? (
          <span className={styles.reason}>{node.reason}</span>
        ) : (
          <span className={styles.meta}>
            {node.task.branch}
            {node.agent ? ` · ${node.agent.state}` : ` · ${node.task.status}`}
          </span>
        )}
      </div>
      <Handle type="source" position={Position.Right} className={styles.handle} />
    </div>
  );
}
