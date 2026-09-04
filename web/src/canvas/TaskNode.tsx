import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import { useDocuments } from '../api/documents';
import type { GraphNode } from '../selectors/graph';
import { nodeTitle } from '../selectors/graph';
import { phaseLabel } from '../protocol/phase';
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
  // One query, not the whole world: a node draws for every task on the desk.
  const documents = useDocuments();
  const documentTitle = documentId
    ? documents.data?.documents.find((d) => d.id === documentId)?.title
    : undefined;
  return (
    <div
      className={styles.node}
      data-testid="task-node"
      data-task-id={node.id}
      data-phase={node.phase ?? undefined}
      data-state={node.state}
      data-focused={focused || undefined}
      data-expanded={expanded || undefined}
    >
      <Handle type="target" position={Position.Left} className={styles.handle} />
      <div className={styles.head}>
        <span className={styles.title}>{nodeTitle(node)}</span>
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
      <div className={styles.status}>
        <span className={styles.dot} aria-hidden="true" />
        {node.reason ? (
          <span className={styles.reason}>{node.reason}</span>
        ) : (
          <span className={styles.state}>{describeState(node.task, node.agent)}</span>
        )}
      </div>
      <div className={styles.meta}>
        {node.showProject && node.task && (
          <span className={styles.project}>{node.task.project}</span>
        )}
        <span className={styles.id}>{nodeIdLine(node)}</span>
        {node.worktree && <span className={styles.worktree}>{node.worktree.nato}</span>}
        {node.phase && <span className={styles.phase}>{phaseLabel(node.phase)}</span>}
      </div>
      <Handle type="source" position={Position.Right} className={styles.handle} />
    </div>
  );
}

/** The id line: a task's bare notebook id, or the head of a free agent's id. */
function nodeIdLine(node: GraphNode): string {
  return node.task ? node.task.notebookId : node.id.slice(0, 8);
}
