import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import type { GraphNode } from '../selectors/graph';
import { describeState } from '../selectors/status';
import { documentTab } from '../selectors/tabs';
import { PanelLink } from '../shell/PanelLink';
import { useAppStore } from '../store/store';
import styles from './TaskNode.module.css';

export type TaskFlowNode = Node<{ node: GraphNode; focused: boolean; expanded: boolean }, 'task'>;

export function TaskNode({ data }: NodeProps<TaskFlowNode>) {
  const { node, focused, expanded } = data;
  const expandNode = useAppStore((s) => s.expandNode);
  const documentId = node.attention.find((a) => a.documentId)?.documentId;
  const documentTitle = useAppStore((s) =>
    documentId ? s.world.documents[documentId]?.title : undefined,
  );
  return (
    <div
      className={styles.node}
      data-testid="task-node"
      data-task-id={node.id}
      data-phase={node.phase}
      data-state={node.state}
      data-focused={focused || undefined}
      data-expanded={expanded || undefined}
    >
      <Handle type="target" position={Position.Left} className={styles.handle} />
      <div className={styles.title}>{node.task.title}</div>
      <div className={styles.line}>
        <span className={styles.id}>{node.id}</span>
        <span className={styles.phase}>{node.phase}</span>
        <span className={styles.spacer} />
        {node.state === 'needs-attention' &&
          (documentId ? (
            <PanelLink
              tab={documentTab(documentId)}
              className={styles.badge}
              aria-label={`needs attention: open ${documentTitle ?? 'the document'}`}
              icon={false}
            >
              !
            </PanelLink>
          ) : (
            <button
              type="button"
              className={styles.badge}
              aria-label="needs attention: expand"
              onClick={(e) => {
                e.stopPropagation();
                expandNode(node.id, false);
              }}
            >
              !
            </button>
          ))}
        {node.state === 'done' && <span className={styles.tick}>✓</span>}
      </div>
      <div className={styles.foot}>
        <span className={styles.dot} aria-hidden="true" />
        {node.reason ? (
          <span className={styles.reason}>{node.reason}</span>
        ) : (
          <span className={styles.state}>{describeState(node.task, node.agent)}</span>
        )}
      </div>
      <Handle type="source" position={Position.Right} className={styles.handle} />
    </div>
  );
}
