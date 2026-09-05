import { useDocuments } from '../api/documents';
import { driftLabel } from '../protocol/progress';
import { phaseLabel } from '../protocol/phase';
import type { GraphNode } from '../selectors/graph';
import { nodeTitle } from '../selectors/graph';
import { documentTab } from '../selectors/tabs';
import { PanelLink } from '../shell/PanelLink';
import styles from './DeckRow.module.css';

/**
 * One node in the deck list.
 *
 * It reads the same three registers the canvas node does — the title, the
 * state in words, then the identity — and carries the same `data-state` and
 * `data-phase`, so it inherits the node's whole state vocabulary rather than
 * inventing a second one. The row is the button: a tap opens the node.
 */
export function DeckRow({ node, onOpen }: { node: GraphNode; onOpen: () => void }) {
  const documentId = node.attention.find((a) => a.documentId)?.documentId;
  const documents = useDocuments();
  const documentTitle = documentId
    ? documents.data?.documents.find((d) => d.id === documentId)?.title
    : undefined;
  return (
    <div
      className={styles.row}
      data-testid="deck-row"
      data-task-id={node.id}
      data-phase={node.phase ?? undefined}
      data-state={node.progress.state}
    >
      <button type="button" className={styles.open} onClick={onOpen}>
        <span className={styles.title}>{nodeTitle(node)}</span>
        <span className={styles.status}>
          <span className={styles.dot} aria-hidden="true" />
          <span className={styles.state}>{node.reason || node.progress.words}</span>
          {node.progress.drift && (
            <span
              className={styles.drift}
              role="img"
              aria-label={driftLabel(node.progress)}
              data-drift={node.progress.drift}
            >
              ▲
            </span>
          )}
        </span>
        <span className={styles.meta}>
          {node.showProject && node.task && (
            <span className={styles.project}>{node.task.project}</span>
          )}
          <span className={styles.id}>
            {node.task ? node.task.notebookId : node.id.slice(0, 8)}
          </span>
          {node.worktree && <span className={styles.worktree}>{node.worktree.nato}</span>}
          {node.phase && <span className={styles.phase}>{phaseLabel(node.phase)}</span>}
        </span>
      </button>
      {/* The badge sits outside the row's own button: it opens the document
          the agent waits on, which is a different destination. */}
      {node.progress.state === 'needs-attention' && documentId && (
        <PanelLink
          tab={documentTab(documentId)}
          className={styles.badge}
          aria-label={`needs attention: open ${documentTitle ?? 'the document'}`}
          icon={false}
        >
          !
        </PanelLink>
      )}
    </div>
  );
}
